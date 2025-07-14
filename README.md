# Dual-View SLaVA-CXR

**Dual-View SLaVA-CXR** is a vision-language model for structured radiology report generation from frontal and lateral chest X-rays. Built on the Re³ (Recognize–Reason–Report) paradigm and extending the original SLaVA-CXR model, this project integrates dual-view vision fusion and leverages CLIP, BiomedCLIP, and Phi-2 for enhanced anatomical reasoning.

---

## 📁 Directory Structure

```bash
├── Data Collection and Preprocessing/
│   ├── Data_collection_Mimic.ipynb
│   ├── Data_preprocess.ipynb
│   ├── Radgraph Based Report Cleaning.ipynb
│   └── train_data_json_gen.ipynb
│
├── Evaluate/
│   ├── Evaluate.ipynb
│   └── Results_IU_Xray/           # Contains evaluation results on IU X-ray dataset
│
├── llava_phi/
│   ├── Dual Slava train.ipynb     # Training pipeline
│   └── generation.ipynb           # Inference/report generation
│
├── requirements.txt
└── README.md
```

---

## 🧠 Key Contributions

- **Dual-Encoder Fusion**: Combines CLIP and BiomedCLIP for each view with learnable weight α:

- **Cross-View Attention**: Enables anatomical reasoning across views:

- **Gated Feature Fusion**:

- **Re³ Pipeline**:
  1. **Recognize**: Generate Findings from images
  2. **Reason**: Infer Impression from Findings
  3. **Report**: Output structured radiology reports

---

## 📊 Evaluation Metrics

| Dataset   | BLEU | ROUGE-L | METEOR | BERT | RadGraph F1 | CheXbert F1 |
| --------- | ---- | ------- | ------ | ---- | ----------- | ----------- |
| MIMIC-CXR | ✅   | ✅      | ✅     | ✅   | ✅          | ✅          |
| IU X-Ray  | ✅   | ✅      | ✅     | ✅   | ✅          | ✅          |

_(Results in `/Evaluate/Results_IU_Xray`)_

---

## 🛠️ Setup

```bash
# Clone repo
git clone https://github.com/Clintonkjkj/Dual-View-Slava-CXR.git
cd Dual-View-Slava-CXR

# Set up virtual environment
python -m venv venv
source venv/bin/activate  # or venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

---

## 🚀 Usage

### Download the model

Huggingface - https://huggingface.co/CKJ26/Dual-View-Slava-Final

### 🏋️ Train the Model

Use `llava_phi/Dual Slava train.ipynb` after preparing data using:

- `Data_collection_Mimic.ipynb`
- `Data_preprocess.ipynb`
- `Radgraph Based Report Cleaning.ipynb`
- `train_data_json_gen.ipynb`

### 📄 Generate Reports

Use `llava_phi/generation.ipynb` with both frontal and lateral views, plus a prompt (e.g., "Generate a radiology report").

---

## 🖼️ Model Architecture

![Architecture](architecture/arch_new_Updated.jpg)

---

## 📚 Citation

```bibtex
@misc{dualviewslava2025,
  title={Dual View SLaVA-CXR: Structured Radiology Reporting via Multi-View Chest X-rays},
  author={Clinton KJ et al.},
  year={2025},
  note={Capstone Project}
}
```

---

## 🧑‍💻 Author

- **Clinton KJ** — [Hugging Face Profile](https://huggingface.co/CKJ26)

---

## 📜 License

This repository is provided for academic research purposes only.
