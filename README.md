# Attention-based Contrastive Learning for Winograd Schemas
[![made-with-python](https://img.shields.io/badge/Made%20with-Python-red.svg)](#python)
[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![REUSE status](https://api.reuse.software/badge/github.com/SAP-samples/emnlp2021-attention-contrastive-learning)](https://api.reuse.software/info/github.com/SAP-samples/emnlp2021-attention-contrastive-learning)


#### News
- **11/24/2021: Provided source code**


In this repository we will provide the source code for the paper [*Attention-based Contrastive Learning for Winograd Schemas*](https://arxiv.org/abs/2109.05108) to be presented at  [EMNLP 2021](https://2021.emnlp.org/). The code is in parts based on the code from [Huggingface Tranformers](https://github.com/huggingface/transformers) and the paper [A Surprisingly Robust Trick for Winograd Schema Challenge](https://github.com/vid-koci/bert-commonsense).

## Abstract

![Schematic Illustration of Attentiomn Contrastive Learning](https://raw.githubusercontent.com/SAP-samples/emnlp2021-attention-contrastive-learning/main/img/attention_contrastive.png)

Self-supervised learning has recently attracted considerable attention in the NLP community for its ability to learn discriminative features using a contrastive objective. This paper investigates whether contrastive learning can be extended to Transfomer attention to tackling the Winograd Schema Challenge. To this end, we propose a novel self-supervised framework, leveraging a *contrastive loss* directly at the level of *self-attention*. Experimental analysis of our attention-based models on multiple datasets demonstrates superior commonsense reasoning capabilities. The proposed approach outperforms all comparable unsupervised approaches while occasionally surpassing supervised ones.

#### Authors:
 - [Tassilo Klein](https://tjklein.github.io/)
 - [Moin Nabi](https://moinnabi.github.io/)

## Requirements
- [Python](https://www.python.org/) (version 3.6 or later)
- [PyTorch](https://pytorch.org/)
- [Huggingface Tranformers](https://github.com/huggingface/transformers)


## Download and Installation

1. Install the requiremennts:

```
conda install --yes --file requirements.txt
```

or

```
pip install -r requirements.txt
```

2. Clone this repository and install dependencies:
```
git clone https://github.com/SAP/acl2020-commonsense-reasoning
cd acl2020-commonsense-reasoning
pip install -r requirements.txt
```

3. Create 'data' sub-directory and download files for PDP, WSC challenge, KnowRef, DPR and WinoGrande:
```
mkdir data
wget https://cs.nyu.edu/faculty/davise/papers/WinogradSchemas/PDPChallenge2016.xml
wget https://cs.nyu.edu/faculty/davise/papers/WinogradSchemas/WSCollection.xml
wget https://raw.githubusercontent.com/aemami1/KnowRef/master/Knowref_dataset/knowref_test.json
wget http://www.hlt.utdallas.edu/~vince/data/emnlp12/train.c.txt
wget http://www.hlt.utdallas.edu/~vince/data/emnlp12/test.c.txt
wget https://storage.googleapis.com/ai2-mosaic/public/winogrande/winogrande_1.1.zip
unzip winogrande_1.1.zip
rm winogrande_1.1.zip
cd ..
```


## How to obtain support

[Create an issue](https://github.com/SAP-samples/emnlp2021-attention-contrastive-learning/issues) in this repository if you find a bug or have questions about the content.
For additional support, [ask a question in SAP Community](https://answers.sap.com/questions/ask.html).

## Citations
If you use this code in your research,
please cite:

```
@proceedings{klein-nabi-2021-attention-contrastive-learning,
    title = "Attention-based Contrastive Learning for Winograd Schemas",
    author = "Klein, Tassilo  and
      Nabi, Moin",
     booktitle={Findings of the Association for Computational Linguistics: {EMNLP}},
   year={2021}
}
```

## License
Copyright (c) 2021 SAP SE or an SAP affiliate company. All rights reserved. This project is licensed under the Apache Software License, version 2.0 except as noted otherwise in the [LICENSE](LICENSES/Apache-2.0.txt) file.
