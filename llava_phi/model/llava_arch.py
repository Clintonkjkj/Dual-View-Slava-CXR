#    Copyright 2023 Haotian Liu
#
#    Licensed under the Apache License, Version 2.0 (the "License");
#    you may not use this file except in compliance with the License.
#    You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS,
#    WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    See the License for the specific language governing permissions and
#    limitations under the License.
# The LlaVa Phi architecture Changed for Accepting Dual view images for Chest Xrays

from abc import ABC, abstractmethod
import torch
import torch.nn as nn
from open_clip import create_model_from_pretrained
from transformers import AutoModel
from .multimodal_encoder.clip_encoder import CLIPVisionTower
from .multimodal_projector.builder import build_vision_projector
from .language_model.configuration_llava_phi import LlavaPhiConfig, LlavaPhiVisionConfig, ProjectorConfig
from llava_phi.constants import IGNORE_INDEX, IMAGE_TOKEN_INDEX, DEFAULT_IMAGE_PATCH_TOKEN, DEFAULT_IM_START_TOKEN, DEFAULT_IM_END_TOKEN


class LlavaMetaModel:
    def __init__(self, config):
        super(LlavaMetaModel, self).__init__(config)
        
        # Original CLIP vision tower
        self.vision_tower = CLIPVisionTower(
            LlavaPhiVisionConfig(**config.vision_config["vision_tower"])
        )
        
        # Initialize medical vision tower placeholder
        self._medical_vision_tower_initialized = False
        self.medical_vision_tower = None
        
        d_model = self.vision_tower.output_dim
        
        # Initialize adapter and other components
        self.med_feature_adapter = nn.Sequential(
            nn.Linear(512, d_model),
            nn.LayerNorm(d_model)
        )
        
        # Learnable fusion weight
        self.fusion_weight = nn.Parameter(torch.tensor(1.0))
        self.fusion_sigmoid = nn.Sigmoid()

        self.cross_attention = nn.MultiheadAttention(
            embed_dim=d_model,
            num_heads=8,
            batch_first=True,
            dropout=0.1
        )

        self.norm = nn.LayerNorm(d_model)

        self.fuse_gate = nn.Sequential(
            nn.Linear(2 * d_model, d_model * 4),
            nn.SiLU(),
            nn.LayerNorm(d_model * 4),
            nn.Linear(d_model * 4, 1),
            nn.Sigmoid()
        )

        self.mm_projector = nn.Sequential(
            nn.Linear(d_model, d_model * 4),
            nn.GELU(),
            nn.Dropout(0.2),
            nn.Linear(d_model * 4, config.hidden_size),
            nn.Tanh()
        )

        self.segment_embedding = nn.Parameter(torch.randn(2, d_model))
        self.pos_embed = nn.Parameter(torch.zeros(1, 768, d_model))
        nn.init.trunc_normal_(self.pos_embed, std=0.02)

    def _init_medical_tower(self, device=None):
        """Initialize medical vision tower on first use"""
        if not self._medical_vision_tower_initialized:
            from open_clip import create_model_from_pretrained
            
            model, _ = create_model_from_pretrained(
                "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
            )
            self.medical_vision_tower = model.visual
            
    
            if device is not None:
                self.medical_vision_tower = self.medical_vision_tower.to(device)
            
           
            for param in self.medical_vision_tower.parameters():
                param.requires_grad = False
                
            self._medical_vision_tower_initialized = True
        
    def get_vision_tower(self):
        vision_tower = getattr(self, 'vision_tower', None)
        if type(vision_tower) is list:
            vision_tower = vision_tower[0]
        return vision_tower


class LlavaMetaForCausalLM(ABC):

    @abstractmethod
    def get_model(self):
        pass

    def get_vision_tower(self):
        return self.get_model().get_vision_tower()

    def encode_images(self, images):
        model = self.get_model()
        
      
        if not model._medical_vision_tower_initialized:
            model._init_medical_tower(device=images.device)
        
       
        frontal_clip = model.vision_tower(images[0].unsqueeze(0))[0][0]
        lateral_clip = model.vision_tower(images[1].unsqueeze(0))[0][0]
        
        
        with torch.no_grad():
            frontal_med = model.medical_vision_tower(images[0].unsqueeze(0))
            lateral_med = model.medical_vision_tower(images[1].unsqueeze(0))
            # print("frontal_med shape:", frontal_med.shape)
            frontal_med = model.med_feature_adapter(frontal_med[0]) 
            lateral_med = model.med_feature_adapter(lateral_med[0])
        
       
        weight = model.fusion_sigmoid(model.fusion_weight)
        frontal = weight * frontal_clip + (1-weight) * frontal_med
        lateral = weight * lateral_clip + (1-weight) * lateral_med

     
        seq_len = frontal.size(0)
        if model.pos_embed.size(1) < seq_len:
            new_pos_embed = torch.zeros(1, seq_len, frontal.size(-1)).to(frontal.device)
            nn.init.trunc_normal_(new_pos_embed, std=0.02)
            model.pos_embed = nn.Parameter(new_pos_embed)
            
        pos_embed = model.pos_embed[:, :seq_len][0]
        frontal = frontal + pos_embed
        lateral = lateral + pos_embed

        frontal = model.norm(frontal)
        lateral = model.norm(lateral)
        
        attn_output, _ = model.cross_attention(
            query=frontal.unsqueeze(0),
            key=lateral.unsqueeze(0),
            value=lateral.unsqueeze(0),
            need_weights=False
        )
        attn_output = attn_output[0]

        combined = torch.cat([frontal, attn_output], dim=-1)
        alpha = model.fuse_gate(combined)
        fused = alpha * frontal + (1 - alpha) * attn_output

        fused_proj = model.mm_projector(fused)
        return fused_proj.unsqueeze(0)


    def prepare_inputs_labels_for_multimodal(
        self, input_ids, attention_mask, past_key_values, labels, images
    ):  
        vision_tower = self.get_vision_tower()
        if vision_tower is None or images is None or input_ids.shape[1] == 1:
            if past_key_values is not None and vision_tower is not None and images is not None and input_ids.shape[1] == 1:
                attention_mask = torch.ones((attention_mask.shape[0], past_key_values[-1][-1].shape[-2] + 1), dtype=attention_mask.dtype, device=attention_mask.device)
            return input_ids, attention_mask, past_key_values, None, labels

        if images.ndim == 5:
            B = images.size(0)
            fused_image_features = []
    
            for b in range(B):
                dual_image = images[b]  # shape: [2, C, H, W]
                fused = self.encode_images(dual_image)  # expect shape: [1, D_proj]
                fused_image_features.append(fused)
            image_features = torch.cat(fused_image_features, dim=0)  # shape: [B, D_proj]
        else:
            image_features = self.encode_images(images)

        new_input_embeds = []
        new_labels = [] if labels is not None else None
        cur_image_idx = 0
        for batch_idx, cur_input_ids in enumerate(input_ids):
            if (cur_input_ids == IMAGE_TOKEN_INDEX).sum() == 0:
                # No multimodal input for this sample — just use regular token embeddings
                cur_input_embeds = self.get_model().embed_tokens(cur_input_ids)
                new_input_embeds.append(cur_input_embeds)
        
                if labels is not None:
                    new_labels.append(labels[batch_idx])
                continue

            image_token_indices = torch.where(cur_input_ids == IMAGE_TOKEN_INDEX)[0]
            cur_new_input_embeds = []
            if labels is not None:
                cur_labels = labels[batch_idx]
                cur_new_labels = []
                assert cur_labels.shape == cur_input_ids.shape
            while image_token_indices.numel() > 0:
                cur_image_features = image_features[cur_image_idx]
                image_token_start = image_token_indices[0]
                if getattr(self.config, 'tune_mm_mlp_adapter', False) and getattr(self.config, 'mm_use_im_start_end', False):
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids[:image_token_start-1]).detach())
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids[image_token_start-1:image_token_start]))
                    cur_new_input_embeds.append(cur_image_features)
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids[image_token_start+1:image_token_start+2]))
                    if labels is not None:
                        cur_new_labels.append(cur_labels[:image_token_start])
                        cur_new_labels.append(torch.full((cur_image_features.shape[0],), IGNORE_INDEX, device=labels.device, dtype=labels.dtype))
                        cur_new_labels.append(cur_labels[image_token_start:image_token_start+1])
                        cur_labels = cur_labels[image_token_start+2:]
                else:
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids[:image_token_start]))
                    cur_new_input_embeds.append(cur_image_features)
                    if labels is not None:
                        cur_new_labels.append(cur_labels[:image_token_start])
                        cur_new_labels.append(torch.full((cur_image_features.shape[0],), IGNORE_INDEX, device=labels.device, dtype=labels.dtype))
                        cur_labels = cur_labels[image_token_start+1:]
                cur_image_idx += 1
                if getattr(self.config, 'tune_mm_mlp_adapter', False) and getattr(self.config, 'mm_use_im_start_end', False):
                    cur_input_ids = cur_input_ids[image_token_start+2:]
                else:
                    cur_input_ids = cur_input_ids[image_token_start+1:]
                image_token_indices = torch.where(cur_input_ids == IMAGE_TOKEN_INDEX)[0]
            if cur_input_ids.numel() > 0:
                if getattr(self.config, 'tune_mm_mlp_adapter', False) and getattr(self.config, 'mm_use_im_start_end', False):
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids).detach())
                else:
                    cur_new_input_embeds.append(self.get_model().embed_tokens(cur_input_ids))
                if labels is not None:
                    cur_new_labels.append(cur_labels)
            cur_new_input_embeds = [x.to(device=self.device) for x in cur_new_input_embeds]
            cur_new_input_embeds = torch.cat(cur_new_input_embeds, dim=0)
            new_input_embeds.append(cur_new_input_embeds)
            if labels is not None:
                cur_new_labels = torch.cat(cur_new_labels, dim=0)
                new_labels.append(cur_new_labels)

        if any(x.shape != new_input_embeds[0].shape for x in new_input_embeds):
            max_len = max(x.shape[0] for x in new_input_embeds)

            new_input_embeds_align = []
            for cur_new_embed in new_input_embeds:
                cur_new_embed = torch.cat((cur_new_embed, torch.zeros((max_len - cur_new_embed.shape[0], cur_new_embed.shape[1]), dtype=cur_new_embed.dtype, device=cur_new_embed.device)), dim=0)
                new_input_embeds_align.append(cur_new_embed)
            new_input_embeds = torch.stack(new_input_embeds_align, dim=0)

            if labels is not None:
                new_labels_align = []
                _new_labels = new_labels
                for cur_new_label in new_labels:
                    cur_new_label = torch.cat((cur_new_label, torch.full((max_len - cur_new_label.shape[0],), IGNORE_INDEX, dtype=cur_new_label.dtype, device=cur_new_label.device)), dim=0)
                    new_labels_align.append(cur_new_label)
                new_labels = torch.stack(new_labels_align, dim=0)

            if attention_mask is not None:
                new_attention_mask = []
                for cur_attention_mask, cur_new_labels, cur_new_labels_align in zip(attention_mask, _new_labels, new_labels):
                    new_attn_mask_pad_left = torch.full((cur_new_labels.shape[0] - labels.shape[1],), True, dtype=attention_mask.dtype, device=attention_mask.device)
                    new_attn_mask_pad_right = torch.full((cur_new_labels_align.shape[0] - cur_new_labels.shape[0],), False, dtype=attention_mask.dtype, device=attention_mask.device)
                    cur_new_attention_mask = torch.cat((new_attn_mask_pad_left, cur_attention_mask, new_attn_mask_pad_right), dim=0)
                    new_attention_mask.append(cur_new_attention_mask)
                attention_mask = torch.stack(new_attention_mask, dim=0)
                assert attention_mask.shape == new_labels.shape
        else:
            new_input_embeds = torch.stack(new_input_embeds, dim=0)
            if labels is not None:
                new_labels  = torch.stack(new_labels, dim=0)

            if attention_mask is not None:
                new_attn_mask_pad_left = torch.full((attention_mask.shape[0], new_input_embeds.shape[1] - input_ids.shape[1]), True, dtype=attention_mask.dtype, device=attention_mask.device)
                attention_mask = torch.cat((new_attn_mask_pad_left, attention_mask), dim=1)
                assert attention_mask.shape == new_input_embeds.shape[:2]

        return None, attention_mask, past_key_values, new_input_embeds, new_labels

    def initialize_vision_tokenizer(self, model_args, tokenizer):
        if model_args.mm_use_im_patch_token:
            tokenizer.add_tokens([DEFAULT_IMAGE_PATCH_TOKEN], special_tokens=True)
            self.resize_token_embeddings(len(tokenizer))

        if model_args.mm_use_im_start_end:
            num_new_tokens = tokenizer.add_tokens([DEFAULT_IM_START_TOKEN, DEFAULT_IM_END_TOKEN], special_tokens=True)
            self.resize_token_embeddings(len(tokenizer))

            if num_new_tokens > 0:
                input_embeddings = self.get_input_embeddings().weight.data
                output_embeddings = self.get_output_embeddings().weight.data

                input_embeddings_avg = input_embeddings[:-num_new_tokens].mean(
                    dim=0, keepdim=True)
                output_embeddings_avg = output_embeddings[:-num_new_tokens].mean(
                    dim=0, keepdim=True)

                input_embeddings[-num_new_tokens:] = input_embeddings_avg
                output_embeddings[-num_new_tokens:] = output_embeddings_avg

            if model_args.tune_mm_mlp_adapter:
                for p in self.get_input_embeddings().parameters():
                    p.requires_grad = True
                for p in self.get_output_embeddings().parameters():
                    p.requires_grad = False
                    
        elif model_args.mm_use_im_patch_token:
            if model_args.tune_mm_mlp_adapter:
                for p in self.get_input_embeddings().parameters():
                    p.requires_grad = False
                for p in self.get_output_embeddings().parameters():
                    p.requires_grad = False
