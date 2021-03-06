#
# SPDX-FileCopyrightText: 2020 SAP SE or an SAP affiliate company
#
# SPDX-License-Identifier: Apache-2.0
#
# coding=utf-8
# Copyright 2018 The Google AI Language Team Authors and The HugginFace Inc. team., 2019 Intelligent Systems Lab, University of Oxford, SAP SE
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""BERT finetuning runner."""
from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import sys
import os
sys.path.append(os.getcwd())
sys.path.append("/home/ubuntu/CommonsenseTransformer/transformers/src/")

from rapidfuzz import fuzz
from rapidfuzz import process

from data_reader import InputExample, DataProcessor
import wandb
from scorer import scorer
from torch import nn, optim
from transformers import PYTORCH_PRETRAINED_BERT_CACHE
from transformers import AdamW, get_linear_schedule_with_warmup
from transformers.modeling_electra import ElectraForMaskedLM, ElectraGeneratorPredictions
from transformers import ElectraTokenizer, ElectraModel, ElectraConfig, ElectraPreTrainedModel
from transformers.modeling_roberta import RobertaLMHead
from transformers import RobertaTokenizer, RobertaModel, RobertaConfig
from transformers.modeling_bert import BertOnlyMLMHead
from transformers import BertPreTrainedModel, BertModel
from transformers import BertTokenizer
from torch.nn import functional as F
from torch.utils.data.distributed import DistributedSampler
from torch.utils.data import TensorDataset, DataLoader, RandomSampler, SequentialSampler
from torch.nn import CrossEntropyLoss
import torch
import numpy as np
import re
from tqdm import tqdm, trange
import pickle
import random
import argparse
import logging
import copy
import json
import csv
import random


#from transformers.modeling_bert import RobertaOnlyMLMHead

#from transformers import BertAdam
wandb.init(project="AMEx-MultiTask")

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(name)s -   %(message)s',
                    datefmt='%m/%d/%Y %H:%M:%S',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP = {
    'roberta-base': "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-base-pytorch_model.bin",
    'roberta-large': "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-pytorch_model.bin",
    'roberta-large-mnli': "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-mnli-pytorch_model.bin",
    'distilroberta-base': "https://s3.amazonaws.com/models.huggingface.co/bert/distilroberta-base-pytorch_model.bin",
    'roberta-base-openai-detector': "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-base-openai-detector-pytorch_model.bin",
    'roberta-large-openai-detector': "https://s3.amazonaws.com/models.huggingface.co/bert/roberta-large-openai-detector-pytorch_model.bin",
}

ELECTRA_PRETRAINED_MODEL_ARCHIVE_MAP = {
    "google/electra-small-generator": "https://cdn.huggingface.co/google/electra-small-generator/pytorch_model.bin",
    "google/electra-base-generator": "https://cdn.huggingface.co/google/electra-base-generator/pytorch_model.bin",
    "google/electra-large-generator": "https://cdn.huggingface.co/google/electra-large-generator/pytorch_model.bin",
    "google/electra-small-discriminator": "https://cdn.huggingface.co/google/electra-small-discriminator/pytorch_model.bin",
    "google/electra-base-discriminator": "https://cdn.huggingface.co/google/electra-base-discriminator/pytorch_model.bin",
    "google/electra-large-discriminator": "https://cdn.huggingface.co/google/electra-large-discriminator/pytorch_model.bin",
}


class EntropyLoss(nn.Module):
    ''' Module to compute entropy loss '''

    def __init__(self, normalize):
        super(EntropyLoss, self).__init__()
        self.normalize = normalize

    def forward(self, x):
        eps = 0.00001
        b = F.softmax(x, dim=1) * torch.log2(F.softmax(x, dim=1)+eps)
        b = b.sum(-1)
        #if any(b.detach().cpu().numpy > 1.0):
        #    print(b)
        if self.normalize:
            b = torch.div(b, np.log2(x.shape[1]))

        #print(b.mean())
        b = -1.0 * b.mean()

        return b


entropy_loss = EntropyLoss(normalize=True)


def entroppy(x):
    b = F.softmax(x, dim=1) * F.log_softmax(x, dim=1)
    b = -1.0 * b.sum(-1).mean()
    return b

#class BertForMaskedLM(PreTrainedBertModel):
#    """BERT model with the masked language modeling head.
#
#    The code is taken from pytorch_pretrain_bert/modeling.py, but the loss function has been changed to return
#    loss for each example separately.
#    """
#    def __init__(self, config):
#        super(BertForMaskedLM, self).__init__(config)
#        self.bert = BertModel(config)
#        self.cls = BertOnlyMLMHead(config, self.bert.embeddings.word_embeddings.weight)
#        self.apply(self.init_bert_weights)
#
#    def forward(self, input_ids, token_type_ids=None, attention_mask=None, masked_lm_labels=None):
#        sequence_output, _,attention = self.bert(input_ids, token_type_ids, attention_mask,
#                                       output_all_encoded_layers=False)
#        prediction_scores = self.cls(sequence_output)
#
#        if masked_lm_labels is not None:
#            loss_fct = CrossEntropyLoss(ignore_index=-1,reduction='none')
#            masked_lm_loss = loss_fct(prediction_scores.permute(0,2,1), masked_lm_labels)
#            return torch.mean(masked_lm_loss,1), attention
#        else:
#            return prediction_scores


class BertForMaskedLM(BertPreTrainedModel):
    r"""
        **masked_lm_labels**: (`optional`) ``torch.LongTensor`` of shape ``(batch_size, sequence_length)``:
            Labels for computing the masked language modeling loss.
            Indices should be in ``[-1, 0, ..., config.vocab_size]`` (see ``input_ids`` docstring)
            Tokens with indices set to ``-1`` are ignored (masked), the loss is only computed for the tokens with labels
            in ``[0, ..., config.vocab_size]``
        **lm_labels**: (`optional`) ``torch.LongTensor`` of shape ``(batch_size, sequence_length)``:
            Labels for computing the left-to-right language modeling loss (next word prediction).
            Indices should be in ``[-1, 0, ..., config.vocab_size]`` (see ``input_ids`` docstring)
            Tokens with indices set to ``-1`` are ignored (masked), the loss is only computed for the tokens with labels
            in ``[0, ..., config.vocab_size]``

    Outputs: `Tuple` comprising various elements depending on the configuration (config) and inputs:
        **masked_lm_loss**: (`optional`, returned when ``masked_lm_labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Masked language modeling loss.
        **ltr_lm_loss**: (`optional`, returned when ``lm_labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Next token prediction loss.
        **prediction_scores**: ``torch.FloatTensor`` of shape ``(batch_size, sequence_length, config.vocab_size)``
            Prediction scores of the language modeling head (scores for each vocabulary token before SoftMax).
        **hidden_states**: (`optional`, returned when ``config.output_hidden_states=True``)
            list of ``torch.FloatTensor`` (one for the output of each layer + the output of the embeddings)
            of shape ``(batch_size, sequence_length, hidden_size)``:
            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        **attentions**: (`optional`, returned when ``config.output_attentions=True``)
            list of ``torch.FloatTensor`` (one for each layer) of shape ``(batch_size, num_heads, sequence_length, sequence_length)``:
            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention heads.

    Examples::

        tokenizer = BertTokenizer.from_pretrained('bert-base-uncased')
        model = BertForMaskedLM.from_pretrained('bert-base-uncased')
        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute")).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, masked_lm_labels=input_ids)
        loss, prediction_scores = outputs[:2]

    """

    def __init__(self, config):
        super(BertForMaskedLM, self).__init__(config)

        self.bert = BertModel(config)
        self.cls = BertOnlyMLMHead(config)

        self.init_weights()

    def get_output_embeddings(self):
        return self.cls.predictions.decoder

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, head_mask=None, inputs_embeds=None,
                masked_lm_labels=None, encoder_hidden_states=None, encoder_attention_mask=None, lm_labels=None, ):

        outputs = self.bert(input_ids,
                            attention_mask=attention_mask,
                            token_type_ids=token_type_ids,
                            position_ids=position_ids,
                            head_mask=head_mask,
                            inputs_embeds=inputs_embeds,
                            encoder_hidden_states=encoder_hidden_states,
                            encoder_attention_mask=encoder_attention_mask)

        sequence_output = outputs[0]
        prediction_scores = self.cls(sequence_output)

        # Add hidden states and attention if they are here
        outputs = (prediction_scores,) + outputs[2:]

        # Although this may seem awkward, BertForMaskedLM supports two scenarios:
        # 1. If a tensor that contains the indices of masked labels is provided,
        #    the cross-entropy is the MLM cross-entropy that measures the likelihood
        #    of predictions for masked words.
        # 2. If `lm_labels` is provided we are in a causal scenario where we
        #    try to predict the next token for each input in the decoder.
        if masked_lm_labels is not None:
            # -1 index = padding token
            loss_fct = CrossEntropyLoss(ignore_index=-1, reduction='none')
            #logger.info(prediction_scores.permute(0,2,1).shape)
            #logger.info(masked_lm_labels.shape)
            masked_lm_loss = loss_fct(
                prediction_scores.permute(0, 2, 1), masked_lm_labels)
            #print((masked_lm_labels > -1).sum(dim=1))
            #print(torch.mean(masked_lm_loss,1).shape)
            #print(torch.div(torch.mean(masked_lm_loss,1),(masked_lm_labels > -1).sum(dim=1,dtype=torch.float32)).shape)
            #logger.info(masked_lm_loss.shape)

            #outputs = (torch.mean(masked_lm_loss,1),) + outputs

            masked_lm_loss_normalized = torch.div(torch.mean(
                masked_lm_loss, 1), (masked_lm_labels > -1).sum(dim=1, dtype=torch.float32))

            masked_lm_loss_normalized[torch.isnan(
                masked_lm_loss_normalized)] = 0.0

            outputs = (masked_lm_loss_normalized,) + outputs

        if lm_labels is not None:
            # we are doing next-token prediction; shift prediction scores and input ids by one
            prediction_scores = prediction_scores[:, :-1, :].contiguous()
            lm_labels = lm_labels[:, 1:].contiguous()
            loss_fct = CrossEntropyLoss(ignore_index=-1)
            ltr_lm_loss = loss_fct(
                prediction_scores.view(-1, self.config.vocab_size), lm_labels.view(-1))
            outputs = (ltr_lm_loss,) + outputs

        # (masked_lm_loss), (ltr_lm_loss), prediction_scores, (hidden_states), (attentions)
        return outputs


class ElectraForMaskedLM(ElectraPreTrainedModel):
    def __init__(self, config):
        super().__init__(config)

        self.electra = ElectraModel(config)
        self.generator_predictions = ElectraGeneratorPredictions(config)

        self.generator_lm_head = nn.Linear(
            config.embedding_size, config.vocab_size)
        self.init_weights()

    def get_output_embeddings(self):
        return self.generator_lm_head

    def forward(
        self,
        input_ids=None,
        attention_mask=None,
        token_type_ids=None,
        position_ids=None,
        head_mask=None,
        inputs_embeds=None,
        masked_lm_labels=None,
    ):
        r"""
        masked_lm_labels (:obj:`torch.LongTensor` of shape :obj:`(batch_size, sequence_length)`, `optional`, defaults to :obj:`None`):
            Labels for computing the masked language modeling loss.
            Indices should be in ``[-100, 0, ..., config.vocab_size]`` (see ``input_ids`` docstring)
            Tokens with indices set to ``-100`` are ignored (masked), the loss is only computed for the tokens with labels
            in ``[0, ..., config.vocab_size]``

    Returns:
        :obj:`tuple(torch.FloatTensor)` comprising various elements depending on the configuration (:class:`~transformers.ElectraConfig`) and inputs:
        masked_lm_loss (`optional`, returned when ``masked_lm_labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Masked language modeling loss.
        prediction_scores (:obj:`torch.FloatTensor` of shape :obj:`(batch_size, sequence_length, config.vocab_size)`)
            Prediction scores of the language modeling head (scores for each vocabulary token before SoftMax).
        hidden_states (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_hidden_states=True``):
            Tuple of :obj:`torch.FloatTensor` (one for the output of the embeddings + one for the output of each layer)
            of shape :obj:`(batch_size, sequence_length, hidden_size)`.

            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        attentions (:obj:`tuple(torch.FloatTensor)`, `optional`, returned when ``config.output_attentions=True``):
            Tuple of :obj:`torch.FloatTensor` (one for each layer) of shape
            :obj:`(batch_size, num_heads, sequence_length, sequence_length)`.

            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention
            heads.

        Examples::

            from transformers import ElectraTokenizer, ElectraForMaskedLM
            import torch

            tokenizer = ElectraTokenizer.from_pretrained('google/electra-small-generator')
            model = ElectraForMaskedLM.from_pretrained('google/electra-small-generator')

            input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute", add_special_tokens=True)).unsqueeze(0)  # Batch size 1
            outputs = model(input_ids, masked_lm_labels=input_ids)

            loss, prediction_scores = outputs[:2]

        """

        generator_hidden_states = self.electra(
            input_ids, attention_mask, token_type_ids, position_ids, head_mask, inputs_embeds
        )
        generator_sequence_output = generator_hidden_states[0]

        prediction_scores = self.generator_predictions(
            generator_sequence_output)
        prediction_scores = self.generator_lm_head(prediction_scores)

        output = (prediction_scores,)

        # Masked language modeling softmax layer
        if masked_lm_labels is not None:
            #loss_fct = nn.CrossEntropyLoss()  # -100 index = padding token
            #loss = loss_fct(prediction_scores.view(-1, self.config.vocab_size), masked_lm_labels.view(-1))

            # -1 index = padding token
            loss_fct = CrossEntropyLoss(ignore_index=-1, reduction='none')

            masked_lm_loss = loss_fct(
                prediction_scores.view(-1, self.config.vocab_size), masked_lm_labels.view(-1))

            masked_lm_loss = loss_fct(
                prediction_scores.permute(0, 2, 1), masked_lm_labels)

            masked_lm_loss_normalized = torch.div(torch.mean(
                masked_lm_loss, 1), (masked_lm_labels > -1).sum(dim=1, dtype=torch.float32))

            masked_lm_loss_normalized[torch.isnan(
                masked_lm_loss_normalized)] = 0.0

            output = (masked_lm_loss_normalized,) + output

            #output = (loss,) + output

        output += generator_hidden_states[1:]
        return output


class RobertaForMaskedLM(BertPreTrainedModel):
    r"""
        **masked_lm_labels**: (`optional`) ``torch.LongTensor`` of shape ``(batch_size, sequence_length)``:
            Labels for computing the masked language modeling loss.
            Indices should be in ``[-1, 0, ..., config.vocab_size]`` (see ``input_ids`` docstring)
            Tokens with indices set to ``-1`` are ignored (masked), the loss is only computed for the tokens with labels
            in ``[0, ..., config.vocab_size]``

    Outputs: `Tuple` comprising various elements depending on the configuration (config) and inputs:
        **loss**: (`optional`, returned when ``masked_lm_labels`` is provided) ``torch.FloatTensor`` of shape ``(1,)``:
            Masked language modeling loss.
        **prediction_scores**: ``torch.FloatTensor`` of shape ``(batch_size, sequence_length, config.vocab_size)``
            Prediction scores of the language modeling head (scores for each vocabulary token before SoftMax).
        **hidden_states**: (`optional`, returned when ``config.output_hidden_states=True``)
            list of ``torch.FloatTensor`` (one for the output of each layer + the output of the embeddings)
            of shape ``(batch_size, sequence_length, hidden_size)``:
            Hidden-states of the model at the output of each layer plus the initial embedding outputs.
        **attentions**: (`optional`, returned when ``config.output_attentions=True``)
            list of ``torch.FloatTensor`` (one for each layer) of shape ``(batch_size, num_heads, sequence_length, sequence_length)``:
            Attentions weights after the attention softmax, used to compute the weighted average in the self-attention heads.

    Examples::

        tokenizer = RobertaTokenizer.from_pretrained('roberta-base')
        model = RobertaForMaskedLM.from_pretrained('roberta-base')
        input_ids = torch.tensor(tokenizer.encode("Hello, my dog is cute")).unsqueeze(0)  # Batch size 1
        outputs = model(input_ids, masked_lm_labels=input_ids)
        loss, prediction_scores = outputs[:2]

    """
    config_class = RobertaConfig
    pretrained_model_archive_map = ROBERTA_PRETRAINED_MODEL_ARCHIVE_MAP
    base_model_prefix = "roberta"

    def __init__(self, config):
        super(RobertaForMaskedLM, self).__init__(config)

        self.roberta = RobertaModel(config)
        self.lm_head = RobertaLMHead(config)

        self.init_weights()

    def get_output_embeddings(self):
        return self.lm_head.decoder

    def forward(self, input_ids=None, attention_mask=None, token_type_ids=None, position_ids=None, head_mask=None, inputs_embeds=None,
                masked_lm_labels=None):
        outputs = self.roberta(input_ids,
                               attention_mask=attention_mask,
                               token_type_ids=token_type_ids,
                               position_ids=position_ids,
                               head_mask=head_mask,
                               inputs_embeds=inputs_embeds)
        sequence_output = outputs[0]
        prediction_scores = self.lm_head(sequence_output)

        # Add hidden states and attention if they are here
        outputs = (prediction_scores,) + outputs[2:]

        if masked_lm_labels is not None:
            #loss_fct = CrossEntropyLoss(ignore_index=-1)

            # -1 index = padding token
            loss_fct = CrossEntropyLoss(ignore_index=-1, reduction='none')

            masked_lm_loss = loss_fct(
                prediction_scores.view(-1, self.config.vocab_size), masked_lm_labels.view(-1))

            masked_lm_loss = loss_fct(
                prediction_scores.permute(0, 2, 1), masked_lm_labels)

            masked_lm_loss_normalized = torch.div(torch.mean(
                masked_lm_loss, 1), (masked_lm_labels > -1).sum(dim=1, dtype=torch.float32))

            masked_lm_loss_normalized[torch.isnan(
                masked_lm_loss_normalized)] = 0.0

            outputs = (masked_lm_loss_normalized,) + outputs

        # (masked_lm_loss), prediction_scores, (hidden_states), (attentions)
        return outputs


def find_sub_list(sl, l):
    results = []
    sll = len(sl)
    for ind in (i for i, e in enumerate(l) if e == sl[0]):
        if l[ind:ind+sll] == sl:
            results.append((ind, ind+sll-1))

    return results


class InputFeatures(object):
    """A single set of features of data."""

    def __init__(self, input_ids_1, input_ids_2, attention_mask_1, attention_mask_2, type_1, type_2, masked_lm_1, masked_lm_2, start, end_1, end_2, source_start_token_1, source_end_token_1, source_start_token_2, source_end_token_2, a1_found, a1_start, a1_len, b1_start, b1_len, a2_found, a2_start, a2_len, b2_start, b2_len, mex, label):
        self.input_ids_1 = input_ids_1
        self.attention_mask_1 = attention_mask_1
        self.type_1 = type_1
        self.masked_lm_1 = masked_lm_1
        #These are only used for train examples
        self.input_ids_2 = input_ids_2
        self.attention_mask_2 = attention_mask_2
        self.type_2 = type_2
        self.masked_lm_2 = masked_lm_2
        self.start = start
        self.end_1 = end_1
        self.end_2 = end_2
        self.source_start_token_1 = source_start_token_1
        self.source_end_token_1 = source_end_token_1
        self.source_start_token_2 = source_start_token_2
        self.source_end_token_2 = source_end_token_2

        self.a1_found = a1_found
        self.a1_start = a1_start
        self.a1_len = a1_len
        self.b1_start = b1_start
        self.b1_len = b1_len

        self.a2_found = a2_found
        self.a2_start = a2_start
        self.a2_len = a2_len
        self.b2_start = b2_start
        self.b2_len = b2_len
        self.mex = mex
        self.label = label


def convert_examples_to_features_train(examples, max_seq_len, tokenizer, mode='oxford'):
    """Loads a data file into a list of `InputBatch`s."""

    features = []
    count = [0, 0]
    for (ex_index, example) in enumerate(examples):
        try:

            # noisy annotation fix

            # Hypen in candidates needs separate handling with insertion of dummy
            remove_dummy = False
            if example.candidate_a.find("'") > -1:
                remove_dummy = True
                example.text_a = example.text_a.replace(example.candidate_a, example.candidate_a.replace("'","###"))

            if example.candidate_b.find("'") > -1:
                remove_dummy = True
                example.text_a = example.text_a.replace(example.candidate_b, example.candidate_b.replace("'","###"))


            if ex_index == 26638:  # ex_index == 10130 or ex_index == 6228 or ex_index == 1008:  # 296
                tmp = 1



            # remove punction without dashand then split
            word_list = example.text_a.lower().replace("'", " ").replace(",", "").replace(";", "").replace(".", "").replace("?", "").replace("!", "").replace(".", "").split()

            if remove_dummy:
                word_list = [x.replace("###","'") for x in word_list]
                example.text_a = example.text_a.replace("###", "'")

            if len(example.candidate_a.split())>1:
                candidate_split = find_sub_list(example.candidate_a.lower().split(), word_list)
                start_index = candidate_split[0][0]
                end_index = candidate_split[0][1]+1
                word_list[start_index:end_index] = [' '.join(word_list[start_index:end_index])]

            if len(example.candidate_b.split())>1:
                candidate_split = find_sub_list(example.candidate_b.lower().split(), word_list)
                start_index = candidate_split[0][0]
                end_index = candidate_split[0][1]+1
                word_list[start_index:end_index] = [' '.join(word_list[start_index:end_index])]

            # check if the first candidate is in the word list

            if not example.candidate_a.lower() in word_list:
                example.candidate_a = process.extract(example.candidate_a, word_list, limit=1)[0][0]


            if not example.candidate_b.lower() in word_list:
                example.candidate_b = process.extract(example.candidate_b, word_list, limit=1)[0][0]





            tokens_sent = tokenizer.tokenize(
                example.text_a.lower(), add_prefix_space=True)
            tokens_a = tokenizer.tokenize(
                example.candidate_a.lower(), add_prefix_space=True)
            tokens_b = tokenizer.tokenize(
                example.candidate_b.lower(), add_prefix_space=True)
            if len(tokens_a) == len(tokens_b):
                count[0] = count[0]+1
            else:
                count[1] = count[1]+1
            tokens_1, type_1, attention_mask_1, masked_lm_1 = [], [], [], []
            tokens_2, type_2, attention_mask_2, masked_lm_2 = [], [], [], []
            tokens_1.append("<s>")
            tokens_2.append("<s>")
            for token in tokens_sent:

                if token.find("_") > -1:
                    start = len(tokens_1)
                    if mode == 'oxford':
                        tokens_1.extend(
                            ["<mask>" for _ in range(len(tokens_a))])
                        tokens_2.extend(
                            ["<mask>" for _ in range(len(tokens_b))])
                    else:
                        tokens_1.append("<mask>")
                        tokens_2.append("<mask>")

                    end_1 = len(tokens_1)
                    end_2 = len(tokens_2)
                else:
                    tokens_1.append(token)
                    tokens_2.append(token)

        except:
            logger.info("Issue with item "+str(ex_index)+"...")
            continue

        token_idx_1 = []
        token_idx_2 = []
        token_counter_1 = 0
        token_counter_2 = 0
        find_tokens_a = True
        find_tokens_b = True

        for idx, token in enumerate(tokens_a):

            if (find_tokens_a and token.lower() == tokens_a[token_counter_1].lower()):
                token_idx_1.append(idx)
                token_counter_1 += 1
                if (len(token_idx_1) >= len(tokens_a)):
                    find_tokens_a = False
            elif find_tokens_a:
                token_idx_1 = []
                token_counter_1 = 0

        for idx, token in enumerate(tokens_b):

            if (find_tokens_b and token.lower() == tokens_b[token_counter_2].lower()):
                token_idx_2.append(idx)
                token_counter_2 += 1
                if (len(token_idx_2) >= len(tokens_b)):
                    find_tokens_b = False
            elif find_tokens_b:
                token_idx_2 = []
                token_counter_2 = 0

        tokens_1 = tokens_1[:max_seq_len-1]  # -1 because of [SEP]
        tokens_2 = tokens_2[:max_seq_len-1]
        if tokens_1[-1] != "</s>":
            tokens_1.append("</s>")
        if tokens_2[-1] != "</s>":
            tokens_2.append("</s>")

        type_1 = max_seq_len*[0]  # We do not do any inference.
        type_2 = max_seq_len*[0]  # These embeddings can thus be ignored

        attention_mask_1 = (len(tokens_1)*[1]) + \
            ((max_seq_len-len(tokens_1))*[0])
        attention_mask_2 = (len(tokens_2)*[1]) + \
            ((max_seq_len-len(tokens_2))*[0])

        #sentences
        input_ids_1 = tokenizer.convert_tokens_to_ids(tokens_1)
        input_ids_2 = tokenizer.convert_tokens_to_ids(tokens_2)
        #replacements
        input_ids_a = tokenizer.convert_tokens_to_ids(tokens_a)
        input_ids_b = tokenizer.convert_tokens_to_ids(tokens_b)

        # max two findings
        a1_found = []
        a1_loc = [-1, -1]
        a1_len = [-1, -1]
        b1_loc = [-1, -1]
        b1_len = [-1, -1]

        a2_found = []
        a2_loc = [-1, -1]
        a2_len = [-1, -1]
        b2_loc = [-1, -1]
        b2_len = [-1, -1]

        # skip first and last token for matching
        #try:
        ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_a], [
                           i.encode('ascii', 'ignore') for i in tokens_1])

        # remove prefix matches if there are any
        if example.candidate_b.startswith(example.candidate_a):
            support_ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_b], [
                i.encode('ascii', 'ignore') for i in tokens_1])

            start_support_ls = [x[0] for x in support_ls]

            ls = [x for x in ls if x[0] not in start_support_ls]



        a1_found.append(len(ls))
        for idx, src in enumerate(ls):
            if idx < 2:
                a1_loc[idx] = src[0]
                a1_len[idx] = src[1]+1

        ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_b], [
                           i.encode('ascii', 'ignore') for i in tokens_1])
        

        # remove prefix matches if there are any
        if example.candidate_a.startswith(example.candidate_b):
            support_ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_a], [
                i.encode('ascii', 'ignore') for i in tokens_1])
            start_support_ls = [x[0] for x in support_ls]

            ls = [x for x in ls if x[0] not in start_support_ls]
        if len(ls) == 0:
            continue
        a1_found.append(len(ls))
        for idx, src in enumerate(ls):
            try:
                if idx < 2:
                    b1_loc[idx] = src[0]
                    b1_len[idx] = src[1]+1
            except:
                tmp = 1

        if len(ls) == 0:
            print([i.encode('ascii', 'ignore') for i in tokens_b])
            print([i.encode('ascii', 'ignore') for i in tokens_1])
            continue

        ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_a], [
                           i.encode('ascii', 'ignore') for i in tokens_2])

        # remove prefix matches if there are any
        if example.candidate_b.startswith(example.candidate_a):
            support_ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_b], [
                i.encode('ascii', 'ignore') for i in tokens_2])

            start_support_ls = [x[0] for x in support_ls]

            ls = [x for x in ls if x[0] not in start_support_ls]

        if len(ls) == 0:
            continue
        a2_found.append(len(ls))
        for idx, src in enumerate(ls):
            if idx < 2:
                a2_loc[idx] = src[0]
                a2_len[idx] = src[1]+1
        ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_b], [
                           i.encode('ascii', 'ignore') for i in tokens_2])

        # remove prefix matches if there are any
        if example.candidate_a.startswith(example.candidate_b):
            support_ls = find_sub_list([i.encode('ascii', 'ignore') for i in tokens_a], [
                i.encode('ascii', 'ignore') for i in tokens_2])

            start_support_ls = [x[0] for x in support_ls]

            ls = [x for x in ls if x[0] not in start_support_ls]

        if len(ls) == 0:
            continue
        a2_found.append(len(ls))
        for idx, src in enumerate(ls):
            if idx < 2:
                b2_loc[idx] = src[0]
                b2_len[idx] = src[1]+1

        for token in tokens_1:
            if token == "<mask>":
                if len(input_ids_a) <= 0:
                    continue  # broken case
                masked_lm_1.append(input_ids_a[0])
                input_ids_a = input_ids_a[1:]
            else:
                masked_lm_1.append(-1)
        while len(masked_lm_1) < max_seq_len:
            masked_lm_1.append(-1)

        for token in tokens_2:
            if token == "<mask>":
                if len(input_ids_b) <= 0:
                    continue  # broken case
                masked_lm_2.append(input_ids_b[0])
                input_ids_b = input_ids_b[1:]
            else:
                masked_lm_2.append(-1)
        while len(masked_lm_2) < max_seq_len:
            masked_lm_2.append(-1)

        # Zero-pad up to the sequence length.
        while len(input_ids_1) < max_seq_len:
            input_ids_1.append(0)
        while len(input_ids_2) < max_seq_len:
            input_ids_2.append(0)
        assert len(input_ids_1) == max_seq_len
        assert len(input_ids_2) == max_seq_len
        assert len(attention_mask_1) == max_seq_len
        assert len(attention_mask_2) == max_seq_len
        assert len(type_1) == max_seq_len
        assert len(type_2) == max_seq_len
        assert len(masked_lm_1) == max_seq_len
        assert len(masked_lm_2) == max_seq_len
        #if len(tokens_a) == len(tokens_b):
        #if example.text_a.lower().find("the monsters at the haunted house") > -1:
        #    tmp = 1
        if input_ids_1[1] == 145 and input_ids_1[2] == 10 and input_ids_1[3] == 3254 and input_ids_1[4] == 16:
            tmp = 1
        features.append(
            InputFeatures(input_ids_1=input_ids_1,
                          input_ids_2=input_ids_2,
                          attention_mask_1=attention_mask_1,
                          attention_mask_2=attention_mask_2,
                          type_1=type_1,
                          type_2=type_2,
                          masked_lm_1=masked_lm_1,
                          masked_lm_2=masked_lm_2, start=start, end_1=end_1, end_2=end_2, source_start_token_1=token_idx_1[0],  source_end_token_1=token_idx_1[-1], source_start_token_2=token_idx_2[0],  source_end_token_2=token_idx_2[-1], a1_found=a1_found, a1_start=a1_loc, a1_len=a1_len, b1_start=b1_loc, b1_len=b1_len, a2_found=a2_found, a2_start=a2_loc, a2_len=a2_len, b2_start=b2_loc, b2_len=b2_len, mex=example.mex, label=example.label))
    logger.info('Ratio: '+str(count[0]/(count[0]+count[1])))
    return features


def convert_examples_to_features_evaluate(examples, max_seq_len, tokenizer):
    """Loads a data file into a list of `InputBatch`s."""

    features = []
    for (ex_index, example) in enumerate(examples):
        # , add_prefix_space=True)
        tokens_a = tokenizer.tokenize(example.candidate_a)
        tokens_sent = tokenizer.tokenize(
            example.text_a)  # , add_prefix_space=True)

        tokens_1, type_1, attention_mask_1, masked_lm_1 = [], [], [], []
        tokens_1.append("<s>")
        for token in tokens_sent:
            if token.find("_") > -1:
                tokens_1.extend(["<mask>" for _ in range(len(tokens_a))])
            else:
                tokens_1.append(token)
        tokens_1 = tokens_1[:max_seq_len-1]  # -1 because of [SEP]
        if tokens_1[-1] != "</s>":
            tokens_1.append("</s>")

        type_1 = max_seq_len*[0]
        attention_mask_1 = (len(tokens_1)*[1]) + \
            ((max_seq_len-len(tokens_1))*[0])
        #sentences
        input_ids_1 = tokenizer.convert_tokens_to_ids(tokens_1)
        #replacements
        input_ids_a = tokenizer.convert_tokens_to_ids(tokens_a)

        for token in tokens_1:
            if token == "<mask>":
                if len(input_ids_a) <= 0:
                    continue  # broken case
                masked_lm_1.append(input_ids_a[0])
                input_ids_a = input_ids_a[1:]
            else:
                masked_lm_1.append(-1)
        while len(masked_lm_1) < max_seq_len:
            masked_lm_1.append(-1)
        # Zero-pad up to the sequence length.
        while len(input_ids_1) < max_seq_len:
            input_ids_1.append(0)
        assert len(input_ids_1) == max_seq_len
        assert len(attention_mask_1) == max_seq_len
        assert len(type_1) == max_seq_len
        assert len(masked_lm_1) == max_seq_len

        features.append(
            InputFeatures(input_ids_1=input_ids_1,
                          input_ids_2=None,
                          attention_mask_1=attention_mask_1,
                          attention_mask_2=None,
                          type_1=type_1,
                          type_2=None,
                          masked_lm_1=masked_lm_1,
                          masked_lm_2=None, start=None, end_1=None, end_2=None, source_start_token_1=None, source_end_token_1=None, source_start_token_2=None, source_end_token_2=None, a1_found=None, a1_start=None, a1_len=None, b1_start=None, b1_len=None, a2_found=None, a2_start=None, a2_len=None, b2_start=None, b2_len=None, mex=None, label=example.label))
    return features


def convert_examples_to_features_train_bert(examples, max_seq_len, tokenizer, mode='oxford'):
    """Loads a data file into a list of `InputBatch`s."""

    features = []
    count = [0, 0]
    for (ex_index, example) in enumerate(examples):
        tokens_sent = tokenizer.tokenize(example.text_a)
        tokens_a = tokenizer.tokenize(example.candidate_a)
        tokens_b = tokenizer.tokenize(example.candidate_b)
        if len(tokens_a) == len(tokens_b):
            count[0] = count[0]+1
        else:
            count[1] = count[1]+1
        tokens_1, type_1, attention_mask_1, masked_lm_1 = [], [], [], []
        tokens_2, type_2, attention_mask_2, masked_lm_2 = [], [], [], []
        tokens_1.append("[CLS]")
        tokens_2.append("[CLS]")
        for token in tokens_sent:

            if token == '_':  # .find("_")>=-1:
                start = len(tokens_1)
                if mode == 'oxford':
                    tokens_1.extend(["[MASK]" for _ in range(len(tokens_a))])
                    tokens_2.extend(["[MASK]" for _ in range(len(tokens_b))])
                else:
                    tokens_1.append("[MASK]")
                    tokens_2.append("[MASK]")

                end_1 = len(tokens_1)
                end_2 = len(tokens_2)
            else:
                tokens_1.append(token)
                tokens_2.append(token)

        token_idx_1 = []
        token_idx_2 = []
        token_counter_1 = 0
        token_counter_2 = 0
        find_tokens_a = True
        find_tokens_b = True

        for idx, token in enumerate(tokens_a):

            if (find_tokens_a and token.lower() == tokens_a[token_counter_1].lower()):
                token_idx_1.append(idx)
                token_counter_1 += 1
                if (len(token_idx_1) >= len(tokens_a)):
                    find_tokens_a = False
            elif find_tokens_a:
                token_idx_1 = []
                token_counter_1 = 0

        for idx, token in enumerate(tokens_b):

            if (find_tokens_b and token.lower() == tokens_b[token_counter_2].lower()):
                token_idx_2.append(idx)
                token_counter_2 += 1
                if (len(token_idx_2) >= len(tokens_b)):
                    find_tokens_b = False
            elif find_tokens_b:
                token_idx_2 = []
                token_counter_2 = 0

        tokens_1 = tokens_1[:max_seq_len-1]  # -1 because of [SEP]
        tokens_2 = tokens_2[:max_seq_len-1]
        if tokens_1[-1] != "[SEP]":
            tokens_1.append("[SEP]")
        if tokens_2[-1] != "[SEP]":
            tokens_2.append("[SEP]")

        type_1 = max_seq_len*[0]  # We do not do any inference.
        type_2 = max_seq_len*[0]  # These embeddings can thus be ignored

        attention_mask_1 = (len(tokens_1)*[1]) + \
            ((max_seq_len-len(tokens_1))*[0])
        attention_mask_2 = (len(tokens_2)*[1]) + \
            ((max_seq_len-len(tokens_2))*[0])

        #sentences
        input_ids_1 = tokenizer.convert_tokens_to_ids(tokens_1)
        input_ids_2 = tokenizer.convert_tokens_to_ids(tokens_2)
        #replacements
        input_ids_a = tokenizer.convert_tokens_to_ids(tokens_a)
        input_ids_b = tokenizer.convert_tokens_to_ids(tokens_b)

        for token in tokens_1:
            if token == "[MASK]":
                if len(input_ids_a) <= 0:
                    continue  # broken case
                masked_lm_1.append(input_ids_a[0])
                input_ids_a = input_ids_a[1:]
            else:
                masked_lm_1.append(-1)
        while len(masked_lm_1) < max_seq_len:
            masked_lm_1.append(-1)

        for token in tokens_2:
            if token == "[MASK]":
                if len(input_ids_b) <= 0:
                    continue  # broken case
                masked_lm_2.append(input_ids_b[0])
                input_ids_b = input_ids_b[1:]
            else:
                masked_lm_2.append(-1)
        while len(masked_lm_2) < max_seq_len:
            masked_lm_2.append(-1)

        # Zero-pad up to the sequence length.
        while len(input_ids_1) < max_seq_len:
            input_ids_1.append(0)
        while len(input_ids_2) < max_seq_len:
            input_ids_2.append(0)
        assert len(input_ids_1) == max_seq_len
        assert len(input_ids_2) == max_seq_len
        assert len(attention_mask_1) == max_seq_len
        assert len(attention_mask_2) == max_seq_len
        assert len(type_1) == max_seq_len
        assert len(type_2) == max_seq_len
        assert len(masked_lm_1) == max_seq_len
        assert len(masked_lm_2) == max_seq_len
        #if len(tokens_a) == len(tokens_b):
        features.append(
            InputFeatures(input_ids_1=input_ids_1,
                          input_ids_2=input_ids_2,
                          attention_mask_1=attention_mask_1,
                          attention_mask_2=attention_mask_2,
                          type_1=type_1,
                          type_2=type_2,
                          masked_lm_1=masked_lm_1,
                          masked_lm_2=masked_lm_2, start=start, end_1=end_1, end_2=end_2, source_start_token_1=token_idx_1[0],  source_end_token_1=token_idx_1[-1], source_start_token_2=token_idx_2[0],  source_end_token_2=token_idx_2[-1]))
    logger.info('Ratio: '+str(count[0]/(count[0]+count[1])))
    return features


def convert_examples_to_features_evaluate_bert(examples, max_seq_len, tokenizer):
    """Loads a data file into a list of `InputBatch`s."""

    features = []
    for (ex_index, example) in enumerate(examples):
        tokens_a = tokenizer.tokenize(example.candidate_a)
        tokens_sent = tokenizer.tokenize(example.text_a)

        tokens_1, type_1, attention_mask_1, masked_lm_1 = [], [], [], []
        tokens_1.append("[CLS]")
        for token in tokens_sent:
            if token == "_":
                tokens_1.extend(["[MASK]" for _ in range(len(tokens_a))])
            else:
                tokens_1.append(token)
        tokens_1 = tokens_1[:max_seq_len-1]  # -1 because of [SEP]
        if tokens_1[-1] != "[SEP]":
            tokens_1.append("[SEP]")

        type_1 = max_seq_len*[0]
        attention_mask_1 = (len(tokens_1)*[1]) + \
            ((max_seq_len-len(tokens_1))*[0])
        #sentences
        input_ids_1 = tokenizer.convert_tokens_to_ids(tokens_1)
        #replacements
        input_ids_a = tokenizer.convert_tokens_to_ids(tokens_a)

        for token in tokens_1:
            if token == "[MASK]":
                if len(input_ids_a) <= 0:
                    continue  # broken case
                masked_lm_1.append(input_ids_a[0])
                input_ids_a = input_ids_a[1:]
            else:
                masked_lm_1.append(-1)
        while len(masked_lm_1) < max_seq_len:
            masked_lm_1.append(-1)
        # Zero-pad up to the sequence length.
        while len(input_ids_1) < max_seq_len:
            input_ids_1.append(0)
        assert len(input_ids_1) == max_seq_len
        assert len(attention_mask_1) == max_seq_len
        assert len(type_1) == max_seq_len
        assert len(masked_lm_1) == max_seq_len

        features.append(
            InputFeatures(input_ids_1=input_ids_1,
                          input_ids_2=None,
                          attention_mask_1=attention_mask_1,
                          attention_mask_2=None,
                          type_1=type_1,
                          type_2=None,
                          masked_lm_1=masked_lm_1,
                          masked_lm_2=None, start=None, end_1=None, end_2=None, source_start_token_1=None, source_end_token_1=None, source_start_token_2=None, source_end_token_2=None))
    return features


def test(processor, args, tokenizer, model, device, global_step=0, tr_loss=0, test_set="wscr-test", verbose=False, output_file=None):
    eval_examples = processor.get_examples(args.data_dir, test_set)
    eval_features = convert_examples_to_features_evaluate(
        eval_examples, args.max_seq_length, tokenizer)
    if verbose:
        logger.info("***** Running evaluation *****")
        logger.info("  Num examples = %d", len(eval_examples))
        logger.info("  Batch size = %d", args.eval_batch_size)
    all_input_ids_1 = torch.tensor(
        [f.input_ids_1 for f in eval_features], dtype=torch.long)
    all_attention_mask_1 = torch.tensor(
        [f.attention_mask_1 for f in eval_features], dtype=torch.long)
    all_segment_ids_1 = torch.tensor(
        [f.type_1 for f in eval_features], dtype=torch.long)
    all_masked_lm_1 = torch.tensor(
        [f.masked_lm_1 for f in eval_features], dtype=torch.long)
    eval_data = TensorDataset(
        all_input_ids_1, all_attention_mask_1, all_segment_ids_1, all_masked_lm_1)
    # Run prediction for full data
    eval_sampler = SequentialSampler(eval_data)
    eval_dataloader = DataLoader(
        eval_data, sampler=eval_sampler, batch_size=args.eval_batch_size)

    model.eval()
    ans_stats = []
    for batch in eval_dataloader:  # tqdm(eval_dataloader,desc="Evaluation"):
        input_ids_1, input_mask_1, segment_ids_1, label_ids_1 = (
            tens.to(device) for tens in batch)
        with torch.no_grad():
            loss, _, _ = model.forward(input_ids_1, token_type_ids=segment_ids_1,
                                       attention_mask=input_mask_1, masked_lm_labels=label_ids_1)


        eval_loss = loss.to('cpu').numpy()
   
        for loss in eval_loss:
            try:
                curr_id = len(ans_stats)
         
                ans_stats.append(
                    (eval_examples[curr_id].guid, eval_examples[curr_id].ex_true, loss))
            except:
                print(curr_id)
                print(len(eval_examples))
                assert False, "error testing"
    if test_set == "gap-test":
        return scorer(ans_stats, test_set, output_file=os.path.join(args.output_dir, "gap-answers.tsv"))
    elif test_set == "wnli":
        return scorer(ans_stats, test_set, output_file=os.path.join(args.output_dir, "WNLI.tsv"))
    else:
        if output_file is not None:
            return scorer(ans_stats, test_set, output_file=os.path.join(args.output_dir, output_file))
        else:
            return scorer(ans_stats, test_set)


def format_attention(attention):
    squeezed = []
    for layer_attention in attention:
        # 1 x num_heads x seq_len x seq_len
        if len(layer_attention.shape) != 4:
            raise ValueError("The attention tensor does not have the correct number of dimensions. Make sure you set "
                             "output_attentions=True when initializing your model.")
        squeezed.append(layer_attention.squeeze(0))
    # num_layers x num_heads x seq_len x seq_len
    if attention[0].shape[0] > 1:
        return torch.stack(squeezed).permute(1, 0, 2, 3, 4)
    else:
        return torch.stack(squeezed).view(1, torch.stack(squeezed).shape[0], torch.stack(squeezed).shape[1], torch.stack(squeezed).shape[2], torch.stack(squeezed).shape[3])


def main():
    parser = argparse.ArgumentParser()

    ## Required parameters
    parser.add_argument("--data_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The input data dir. Should contain the files for the task.")
    parser.add_argument("--no_cuda", action='store_true',
                        help="Avoid using CUDA when available")
    parser.add_argument("--loadcachedfeatures", action='store_true',
                        help="Whether to load cached features")
    parser.add_argument("--ignore_bestacc", action='store_true',
                        help="Ignore old best accuracy file")
    parser.add_argument("--bert_model", default=None, type=str, required=True,
                        help="Bert pre-trained model selected in the list: bert-base-uncased, "
                             "bert-large-uncased, bert-base-cased, bert-base-multilingual, bert-base-chinese.")
    parser.add_argument("--task_name",
                        default=None,
                        type=str,
                        required=True,
                        help="The name of the task to train.")
    parser.add_argument("--output_dir",
                        default=None,
                        type=str,
                        required=True,
                        help="The output directory where the model checkpoints will be written.")

    parser.add_argument("--cache_dir",
                        default="cache/",
                        type=str,
                        help="The cahce directory where feature files will be written.")
    parser.add_argument("--tpc_exp",
                        default=2.0,
                        type=float,
                        help="Twin-pair consistency exponentation")

    ## Other parameters
    parser.add_argument("--max_seq_length",
                        default=128,
                        type=int,
                        help="The maximum total input sequence length after WordPiece tokenization. \n"
                             "Sequences longer than this will be truncated, and sequences shorter \n"
                             "than this will be padded.")
    parser.add_argument("--do_train",
                        default=False,
                        action='store_true',
                        help="Whether to run training.")
    parser.add_argument("--do_eval",
                        default=False,
                        action='store_true',
                        help="Whether to run eval on the dev set.")
    parser.add_argument("--alpha_param",
                        default=10,
                        type=float,
                        help="Discriminative penalty hyper-parameter.")
    parser.add_argument("--gamma_param",
                        default=20,
                        type=float,
                        help="Mutual exclusivity strength hyper-parameter.")
    parser.add_argument("--AMEX_layers",
                        default=1,
                        type=int,
                        help="Which of the last layers of the transformer stack should be considered. 1: last layer, 2: last two layers, etc..")
    parser.add_argument("--beta_param",
                        default=0.4,
                        type=float,
                        help="Discriminative intolerance interval hyper-parameter.")
    parser.add_argument("--lambda_param",
                        default=1.0,
                        type=float,
                        help="AMEX weight parameter")
    
    parser.add_argument("--train_batch_size",
                        default=32,
                        type=int,
                        help="Total batch size for training.")
    parser.add_argument("--eval_batch_size",
                        default=32,
                        type=int,
                        help="Total batch size for eval.")
    parser.add_argument("--learning_rate",
                        default=3e-5,
                        type=float,
                        help="The initial learning rate for Adam.")
    parser.add_argument("--weight_decay", default=0.0, type=float,
                        help="Weight deay if we apply some.")
    parser.add_argument("--adam_epsilon", default=1e-8, type=float,
                        help="Epsilon for Adam optimizer.")
    parser.add_argument("--max_grad_norm", default=1.0, type=float,
                        help="Max gradient norm.")
    parser.add_argument("--num_train_epochs",
                        default=1.0,
                        type=float,
                        help="Total number of training epochs to perform.")
    parser.add_argument("--warmup_steps", default=0, type=int,
                        help="Linear warmup over warmup_steps.")
    parser.add_argument("--local_rank",
                        type=int,
                        default=-1,
                        help="local_rank for distributed training on gpus")
    parser.add_argument('--seed',
                        type=int,
                        default=42,
                        help="random seed for initialization")
    parser.add_argument('--gradient_accumulation_steps',
                        type=int,
                        default=1,
                        help="Number of updates steps to accumulate before performing a backward/update pass.")
    parser.add_argument('--load_from_file',
                        type=str,
                        default=None,
                        help="Path to the file with a trained model. Default means bert-model is used. Size must match bert-model.")

    parser.add_argument('--fp16', action='store_true',
                        help="Whether to use 16-bit (mixed) precision (through NVIDIA apex) instead of 32-bit")

    parser.add_argument('--shuffle', action='store_true',
                        help="Whether to shuffle elements to avoid potential bias.")

    args = parser.parse_args()

    logger.info('Command Line: '+' '.join(sys.argv[1:]))

    wandb.config.update(args)

    wandb.config.update({"Command Line": ' '.join(sys.argv[1:])})

    if args.local_rank == -1 or args.no_cuda:
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        args.n_gpu = torch.cuda.device_count()
    else:
        device = torch.device("cuda", args.local_rank)
        args.n_gpu = 1
        # Initializes the distributed backend which will take care of sychronizing nodes/GPUs
        torch.distributed.init_process_group(backend='nccl')
    #logger.info("device %s n_gpu %d distributed training %r", device, n_gpu, bool(args.local_rank != -1))

    logger.warning("Process rank: %s, device: %s, n_gpu: %s, distributed training: %s, 16-bits training: %s",
                   args.local_rank, device, args.n_gpu, bool(args.local_rank != -1), args.fp16)

    if args.gradient_accumulation_steps < 1:
        raise ValueError("Invalid gradient_accumulation_steps parameter: {}, should be >= 1".format(
            args.gradient_accumulation_steps))

    args.train_batch_size = int(
        args.train_batch_size / args.gradient_accumulation_steps)

    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    if args.n_gpu > 0:
        torch.cuda.manual_seed_all(args.seed)

    if not args.do_train and not args.do_eval:
        raise ValueError(
            "At least one of `do_train` or `do_eval` must be True.")

    wandb.run.save()

    args.output_dir = (os.path.join(args.output_dir, wandb.run.name))

    if not os.path.exists(args.output_dir):
        os.makedirs(args.output_dir, exist_ok=True)

    if not os.path.exists(args.cache_dir):
        os.makedirs(args.cache_dir, exist_ok=True)

    task_name = args.task_name.lower()

    processor = DataProcessor()

    # Load pretrained model and tokenizer
    if args.local_rank not in [-1, 0]:
        # Barrier to make sure only the first process in distributed training download model & vocab
        torch.distributed.barrier()

    #tokenizer = BertTokenizer.from_pretrained(args.bert_model)
    tokenizer = RobertaTokenizer.from_pretrained(args.bert_model)
    #tokenizer = ElectraTokenizer.from_pretrained(args.bert_model)


    train_examples = None
    num_train_steps = None
    if args.do_train and (not args.loadcachedfeatures or not (os.path.exists(os.path.join(args.cache_dir, "cachedfeatures-"+task_name+".p")))):
        train_name = {"gap": "gap-train",
                      "wikicrem": "wikicrem-train",
                      "dpr": "dpr-train-small",
                      "wscr": "wscr-train",
                      "winogrande-xl": "winogrande-xl-train",
                      "winogrande-l": "winogrande-l-train",
                      "winogrande-m": "winogrande-m-train",
                      "winogrande-s": "winogrande-s-train",
                      "winogrande-xs": "winogrande-xs-train",
                      "all": "all",
                      'wgdpr': 'wgdpr',
                      "maskedwiki": "maskedwiki",
                      "winogrande-xl-biased": "winogrande-xl-biased-train"
                      }[task_name]

        if task_name == "all":
            train_examples = processor.get_examples(
                args.data_dir, "dpr-train")+processor.get_examples(args.data_dir, "gap-train")
        elif task_name == 'wgdpr':
            train_examples = processor.get_examples(
                args.data_dir, "dpr-train")+processor.get_examples(args.data_dir, "winogrande-xl-train")
        else:
            train_examples = processor.get_examples(args.data_dir, train_name)


        if args.shuffle:
            logger.info('Shuffling training data ...')
            for i in range(0, len(train_examples), 2):
  
                if random.choices([0, 1]) == [0]:
                    candidate_a = copy.deepcopy(train_examples[i].candidate_a)
                    candidate_b = copy.deepcopy(train_examples[i].candidate_b)
                    label_i = copy.deepcopy(train_examples[i].label)
           
                    train_examples[i].candidate_a = candidate_b
                    train_examples[i].candidate_b = candidate_a
           
                    candidate_a = copy.deepcopy(
                        train_examples[i+1].candidate_a)
                    candidate_b = copy.deepcopy(
                        train_examples[i+1].candidate_b)
                    train_examples[i+1].candidate_a = candidate_b
                    train_examples[i+1].candidate_b = candidate_a
                    
                    train_examples[i].label = 2
                    train_examples[i+1].label = 2
                else:
                    train_examples[i].label = 1
                    train_examples[i+1].label = 1


    if args.do_train and args.loadcachedfeatures:
        logger.info("Loading cached features from: " +
                    os.path.join(args.cache_dir, "cachedfeatures-"+task_name+".p"))
        train_features = pickle.load(
            open(os.path.join(args.cache_dir, "cachedfeatures-"+task_name+".p"), "rb"))
        num_train_steps = int(
            len(train_features) / args.train_batch_size / args.gradient_accumulation_steps * args.num_train_epochs)
    else:
        num_train_steps = int(
            len(train_examples) / args.train_batch_size / args.gradient_accumulation_steps * args.num_train_epochs)

    # Prepare model
    if args.load_from_file is None:
        #model = BertForMaskedLM.from_pretrained(args.bert_model,
        #            cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)
        model = RobertaForMaskedLM.from_pretrained(args.bert_model,
                                                   cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)

        #model = ElectraForMaskedLM.from_pretrained(args.bert_model,
        #            cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True, hidden_dropout_prob=0.001, attention_probs_dropout_prob=0.001)
    else:
        #model = BertForMaskedLM.from_pretrained(args.bert_model,
        #            cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)
        #model = RobertaForMaskedLM.from_pretrained(args.bert_model,
        #            cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)
        #model = ElectraForMaskedLM.from_pretrained(args.bert_model,
        #            cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True, hidden_dropout_prob=0.001, attention_probs_dropout_prob=0.001)
        model = RobertaForMaskedLM.from_pretrained(args.load_from_file,
                                                   cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)

    model.to(device)

    if args.local_rank == 0:
        # End of barrier to make sure only the first process in distributed training download model & vocab
        torch.distributed.barrier()

    if False:  # not args.load_from_file is None:
        logger.info('loading model from file ...')
        model_dict = torch.load(args.load_from_file)

        if True:
            old_keys = []
            new_keys = []
            for key in model_dict.keys():
                new_key = None
                if 'gamma' in key:
                    new_key = key.replace('gamma', 'weight')
                if 'beta' in key:
                    new_key = key.replace('beta', 'bias')
                if new_key:
                    old_keys.append(key)
                    new_keys.append(new_key)
            for old_key, new_key in zip(old_keys, new_keys):
                model_dict[new_key] = model_dict.pop(old_key)

            new_dict = dict()
            for k, v in model_dict.items():
                if k.find('module.') > -1:
                    new_dict[re.sub('^%s' % 'module.', '', k)] = v

        model.load_state_dict(new_dict)
        model.to(device)

    # Prepare optimizer
    param_optimizer = list(model.named_parameters())

    no_decay = ['bias', 'gamma', 'beta']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if not any(
            nd in n for nd in no_decay)], 'weight_decay_rate': args.weight_decay},
        {'params': [p for n, p in param_optimizer if any(
            nd in n for nd in no_decay)], 'weight_decay_rate': 0.0}
    ]

    if args.do_train:
        t_total = num_train_steps
        if args.local_rank != -1:
            t_total = t_total // torch.distributed.get_world_size()


    if args.do_train:
        optimizer = AdamW(optimizer_grouped_parameters,
                          lr=args.learning_rate, eps=args.adam_epsilon)
        scheduler = get_linear_schedule_with_warmup(
            optimizer, num_warmup_steps=args.warmup_steps, num_training_steps=t_total)  # PyTorch scheduler

    if args.fp16:
        # apex
        try:
            from apex import amp
        except ImportError:
            raise ImportError(
                "Please install apex from https://www.github.com/nvidia/apex to use fp16 training.")
        model, optimizer = amp.initialize(model, optimizer, opt_level='O1')

    if args.local_rank != -1:
        model = torch.nn.parallel.DistributedDataParallel(model, device_ids=[args.local_rank],
                                                          output_device=args.local_rank)
    else:
        model = torch.nn.DataParallel(model)

    global_step = 0
    tr_loss, nb_tr_steps = 0, 1
    if args.do_train:
        logger.info("***** Running training *****")
        if not args.loadcachedfeatures or not (os.path.exists(os.path.join(args.cache_dir, "cachedfeatures-"+task_name+".p"))):
            logger.info("  Num examples = %d", len(train_examples))
            train_features = convert_examples_to_features_train(
                train_examples, args.max_seq_length, tokenizer, mode='SAP')
            
            pickle.dump(train_features, open(os.path.join(
                args.cache_dir, "cachedfeatures-"+task_name+".p"), "wb"))

        if len(train_features) % 2 == 1:
            logger.info('Uneven number of features. Dropping last one .....')
            del train_features[-1]

        logger.info("  Num features = %d", len(train_features))
        logger.info("  Batch size = %d", args.train_batch_size)
        logger.info("  Num steps = %d", num_train_steps)
        all_labels = torch.tensor([f.label for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_input_ids_1 = torch.tensor([f.input_ids_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_input_ids_2 = torch.tensor([f.input_ids_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_attention_mask_1 = torch.tensor([f.attention_mask_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_attention_mask_2 = torch.tensor([f.attention_mask_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_segment_ids_1 = torch.tensor([f.type_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_segment_ids_2 = torch.tensor([f.type_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_masked_lm_1 = torch.tensor([f.masked_lm_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_masked_lm_2 = torch.tensor([f.masked_lm_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)
        all_start = torch.tensor([f.start for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_end_1 = torch.tensor([f.end_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_end_2 = torch.tensor([f.end_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_start_1 = torch.tensor([f.source_start_token_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_start_2 = torch.tensor([f.source_start_token_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_end_1 = torch.tensor([f.source_end_token_1 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_end_2 = torch.tensor([f.source_end_token_2 for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)

        all_source_found_1 = torch.tensor([f.a1_found[0] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_found_2 = torch.tensor([f.a1_found[1] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_loc_a1_1 = torch.tensor([f.a1_start[0] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_len_a1_1 = torch.tensor([f.a1_len[0] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_loc_a1_2 = torch.tensor([f.a1_start[1] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_len_a1_2 = torch.tensor([f.a1_len[1] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_loc_b1_1 = torch.tensor([f.b1_start[0] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_len_b1_1 = torch.tensor([f.b1_len[0] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_loc_b1_2 = torch.tensor([f.b1_start[1] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_len_b1_2 = torch.tensor([f.b1_len[1] for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.int16)
        all_source_mex = torch.tensor([f.mex for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.bool)
        all_source_idx = torch.tensor([index for index, f in enumerate(
            train_features) if index % 2 == 0], dtype=torch.long)

        
        _all_labels = torch.tensor([f.label for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_input_ids_1 = torch.tensor([f.input_ids_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_input_ids_2 = torch.tensor([f.input_ids_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_attention_mask_1 = torch.tensor([f.attention_mask_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_attention_mask_2 = torch.tensor([f.attention_mask_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_segment_ids_1 = torch.tensor([f.type_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_segment_ids_2 = torch.tensor([f.type_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_masked_lm_1 = torch.tensor([f.masked_lm_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_masked_lm_2 = torch.tensor([f.masked_lm_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        _all_start = torch.tensor([f.start for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_end_1 = torch.tensor([f.end_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_end_2 = torch.tensor([f.end_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_start_1 = torch.tensor([f.source_start_token_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_start_2 = torch.tensor([f.source_start_token_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_end_1 = torch.tensor([f.source_end_token_1 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_end_2 = torch.tensor([f.source_end_token_2 for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)

        _all_source_found_1 = torch.tensor([f.a1_found[0] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_found_2 = torch.tensor([f.a1_found[1] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_loc_a1_1 = torch.tensor([f.a1_start[0] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_len_a1_1 = torch.tensor([f.a1_len[0] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_loc_a1_2 = torch.tensor([f.a1_start[1] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_len_a1_2 = torch.tensor([f.a1_len[1] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_loc_b1_1 = torch.tensor([f.b1_start[0] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_len_b1_1 = torch.tensor([f.b1_len[0] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_loc_b1_2 = torch.tensor([f.b1_start[1] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_len_b1_2 = torch.tensor([f.b1_len[1] for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.int16)
        _all_source_mex = torch.tensor([f.mex for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.bool)
        _all_source_idx = torch.tensor([index for index, f in enumerate(
            train_features) if index % 2 == 1], dtype=torch.long)
        
        
        
        train_data = TensorDataset(
            all_labels, all_input_ids_1, all_input_ids_2, all_attention_mask_1, all_attention_mask_2, all_segment_ids_1, all_segment_ids_2, all_masked_lm_1, all_masked_lm_2,
                                   _all_labels, _all_input_ids_1, _all_input_ids_2, _all_attention_mask_1, _all_attention_mask_2, _all_segment_ids_1, _all_segment_ids_2, _all_masked_lm_1, _all_masked_lm_2, all_start, all_end_1, all_end_2, all_source_start_1, all_source_end_1, all_source_start_2, all_source_end_2, _all_start, _all_end_1, _all_end_2, _all_source_start_1, _all_source_end_1, _all_source_start_2, _all_source_end_2, all_source_found_1, all_source_found_2, all_source_loc_a1_1, all_source_len_a1_1, all_source_loc_a1_2, all_source_len_a1_2, all_source_loc_b1_1, all_source_len_b1_1, all_source_loc_b1_2, all_source_len_b1_2, all_source_mex, all_source_idx,   _all_source_found_1, _all_source_found_2, _all_source_loc_a1_1, _all_source_len_a1_1, _all_source_loc_a1_2, _all_source_len_a1_2, _all_source_loc_b1_1, _all_source_len_b1_1, _all_source_loc_b1_2, _all_source_len_b1_2, _all_source_mex, _all_source_idx)
        if args.local_rank == -1:
            train_sampler = RandomSampler(train_data)
        else:
            train_sampler = DistributedSampler(train_data)
            
        train_dataloader = DataLoader(
            train_data, sampler=train_sampler, batch_size=args.train_batch_size)
        validation_name = {"gap": "gap-dev",
                           "wikicrem": "wikicrem-dev",
                           "dpr": "dpr-dev-small",
                           "all": "all",
                           "maskedwiki": "wscr-test",
                           "winogrande-xl": "winogrande-dev",
                           "winogrande-l": "winogrande-dev",
                           "winogrande-m": "winogrande-dev",
                           "winogrande-s": "winogrande-dev",
                           "winogrande-xs": "winogrande-dev",
                           'wgdpr': 'winogrande-dev',
                           "wscr": "wscr-test",
                           "winogrande-xl-biased": "winogrande-dev"
                           }[task_name]

        model.train()
        # This prevents overwriting if several scripts are running at the same time (for hyper-parameter search)
        try:
            if args.ignore_bestacc:
                best_accuracy = 0
            else:
                best_accuracy = float(
                    list(open(os.path.join(args.output_dir, "best_accuracy.txt"), 'r'))[0])
        except:
            best_accuracy = 0

        second_chance = 0

        for it in trange(int(args.num_train_epochs), desc="Epoch"):
            tr_loss = 0
            tr_accuracy = 0
            nb_tr_examples, nb_tr_steps = 0, 0

            #print(it)
            if it == 0:
                acc = test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss /
                           nb_tr_steps if nb_tr_steps > 0 else 0, test_set=validation_name, verbose=True)
                logger.info("Initial Eval: {}\t{}\n".format(nb_tr_steps, acc))
                wandb.log({'epoch': it, 'accuracy': acc, "steps": nb_tr_steps})
            for step, batch in enumerate(tqdm(train_dataloader)):
               
                
                
                input_labels, input_ids_1, input_ids_2, input_mask_1, input_mask_2, segment_ids_1, segment_ids_2, label_ids_1, label_ids_2, _input_labels, _input_ids_1, _input_ids_2, _input_mask_1, _input_mask_2, _segment_ids_1, _segment_ids_2, _label_ids_1, _label_ids_2, target_start, target_end_1, target_end_2, source_start_1, source_end_1, source_start_2, source_end_2, _target_start, _target_end_1, _target_end_2, _source_start_1, _source_end_1, _source_start_2, _source_end_2, source_found_1, source_found_2, source_loc_a_1, source_len_a_1, source_loc_a_2, source_len_a_2, source_loc_b_1, source_len_b_1, source_loc_b_2, source_len_b_2, source_mex, input_idx,   _source_found_1, _source_found_2, _source_loc_a_1, _source_len_a_1, _source_loc_a_2, _source_len_a_2, _source_loc_b_1, _source_len_b_1, _source_loc_b_2, _source_len_b_2, _source_mex, _input_idx = (
                    tens.to(device) if i < 16 else tens for i, tens in enumerate(batch))
                
                
                loss_1, _, attn_1 = model.forward(
                    input_ids_1, token_type_ids=segment_ids_1, attention_mask=input_mask_1, masked_lm_labels=label_ids_1)
                loss_2, _, attn_2 = model.forward(
                    input_ids_2, token_type_ids=segment_ids_2, attention_mask=input_mask_2, masked_lm_labels=label_ids_2)

                attn_1 = format_attention(attn_1)
                attn_2 = format_attention(attn_2)

                _loss_1, _, _attn_1 = model.forward(
                    _input_ids_1, token_type_ids=_segment_ids_1, attention_mask=_input_mask_1, masked_lm_labels=_label_ids_1)
                _loss_2, _, _attn_2 = model.forward(
                    _input_ids_2, token_type_ids=_segment_ids_2, attention_mask=_input_mask_2, masked_lm_labels=_label_ids_2)

                _attn_1 = format_attention(_attn_1)
                _attn_2 = format_attention(_attn_2)


                # how often candidate A, and candidate B was found
                source_found = [source_found_1, source_found_2]

                # candidate A (first position, *optional second time)
                source_loc_a = [source_loc_a_1, source_loc_a_2]
                # candidate B (first position, *optional second time)
                source_loc_b = [source_loc_b_1, source_loc_b_2]

                source_len_a = [source_len_a_1, source_len_a_2]
                source_len_b = [source_len_b_1, source_len_b_2]

                # how often constrastive candidate A, and candidate B was found
                _source_found = [_source_found_1, _source_found_2]

                # contrastive candidate A (first position, *optional second time)
                _source_loc_a = [_source_loc_a_1, _source_loc_a_2]
                # contrastive candidate B (first position, *optional second time)
                _source_loc_b = [_source_loc_b_1, _source_loc_b_2]

                _source_len_a = [_source_len_a_1, _source_len_a_2]
                _source_len_b = [_source_len_b_1, _source_len_b_2]

              
                num_heads = 16

                att_reg = torch.zeros(1).cuda()
                
                
                    


                # AMEX routine
                # Attention-based Contrastive Learning for Winograd Schemas (EMNLP'21)
                # 
                if True:
              
                    for sample in range(loss_1.shape[0]):

                        # only perform AMEX, if data at location is mutual exclusive
                        if source_mex[sample] == True and _source_mex[sample] == True:
                            tmp_list = []
                            tmp_vec = [[torch.zeros(args.AMEX_layers*num_heads).cuda(), torch.zeros(args.AMEX_layers*num_heads).cuda()], [
                                torch.zeros(args.AMEX_layers*num_heads).cuda(), torch.zeros(args.AMEX_layers*num_heads).cuda()]]
                            for candidate in range(2):

                                start = [source_loc_a, source_loc_b]
                                end = [source_len_a, source_len_b]

                                attn = [attn_1, attn_2]
                               
                                # taking the min of 2, is because we only save up to two positions for attention, it is a hack, but fine for 99,99% of the data
                                for mentioning in range(min([2,source_found[candidate][sample].item()])):

                                    assert target_end_1[sample] == target_end_2[sample], "Use single MASK mode"
                                  
                                    tmp_vec[0][candidate] = tmp_vec[0][candidate] + (attn[candidate][sample, -AMEX_layers, :, slice(target_start[sample], target_start[sample]+_target_end_1[sample], 1), slice(
                                        start[candidate][mentioning][sample], end[candidate][mentioning][sample], 1)].sum(axis=3).sum(axis=2).flatten() / source_found[candidate][sample].item())

                                start = [_source_loc_a, _source_loc_b]
                                end = [_source_len_a, _source_len_b]
                                attn = [_attn_1, _attn_2]
                                for i in range(_source_found[candidate][sample]):

                                    assert _target_end_1[sample] == _target_end_2[sample], "Use single MASK mode"

                                    tmp_vec[1][candidate] = tmp_vec[1][candidate] + (attn[candidate][sample, -AMEX_layers:, :, slice(_target_start[sample], _target_start[sample]+_target_end_1[sample], 1), slice(
                                        start[candidate][mentioning][sample], end[candidate][mentioning][sample], 1)].sum(axis=3).sum(axis=2).flatten() / _source_found[candidate][sample].item())

                       
                            tmp_att_reg = torch.zeros(1).cuda()
                            for i in range(AMEX_layers*num_heads):

                                joint_exp_1 = tmp_vec[0][0][i] + \
                                    tmp_vec[0][1][i]+0.001
                                cexp_11 = tmp_vec[0][0][i]
                                cexp_12 = tmp_vec[0][1][i]

                                joint_exp_2 = tmp_vec[1][0][i] + \
                                    tmp_vec[1][1][i]+0.001
                                cexp_21 = tmp_vec[1][0][i]
                                cexp_22 = tmp_vec[1][1][i]

                            

                                # twin consistency terms

                                a = cexp_11/joint_exp_1
                                b = cexp_21/joint_exp_2

                                #term_1 = torch.abs(a-0.5) + torch.abs(b-0.5) + (1-torch.abs(a-b) + 1-torch.abs((1-a)-(1-b)))
                                term_1 = torch.pow(torch.abs(a-0.5),args.tpc_exp) + torch.pow(torch.abs(b-0.5),args.tpc_exp) + (1-torch.pow(torch.abs(a-b),args.tpc_exp) + 1-torch.pow(torch.abs((1-a)-(1-b)),args.tpc_exp))

                                a = cexp_12/joint_exp_1
                                b = cexp_22/joint_exp_2
                                #term_2 = torch.abs(a-0.5) + torch.abs(b-0.5) + (1-torch.abs(a-b) + 1-torch.abs((1-a)-(1-b)))
                                term_2 = torch.pow(torch.abs(a-0.5), args.tpc_exp) + torch.pow(torch.abs(b-0.5), args.tpc_exp) + (
                                    1-torch.pow(torch.abs(a-b), args.tpc_exp) + 1-torch.pow(torch.abs((1-a)-(1-b)), args.tpc_exp))

                                if torch.isnan(term_1) or torch.isinf(term_1) or torch.isnan(term_2) or torch.isinf(term_2):
                                    #logger.info([cexp_11, cexp_12, cexp_21, cexp_22])
                                    logger.error("NaN or Inf")
                                    term_1 = 0.0
                                    term_2 = 0.0

                                tmp_att_reg = tmp_att_reg + \
                                    (-1.*((term_1) + (term_2)))  

                         

                            att_reg = att_reg + tmp_att_reg

              
                        
                # MEx
                # Contrastive Self-Supervised Learning for Commonsense Reasoning (ACL'2020)
                if False:

                    MEx_margin = args.alpha_param * torch.max(torch.zeros(loss_1.size(), device=device), torch.ones(loss_1.size(), device=device)*args.beta_param + loss_1 - loss_2.mean(
                    )) + args.alpha_param * torch.max(torch.zeros(loss_2.size(), device=device), torch.ones(loss_2.size(), device=device)*args.beta_param + loss_2 - loss_1.mean())

                    MEx_margin += args.alpha_param * torch.max(torch.zeros(_loss_1.size(), device=device), torch.ones(_loss_1.size(), device=device)*args.beta_param + _loss_1 - _loss_2.mean(
                    )) + args.alpha_param * torch.max(torch.zeros(_loss_2.size(), device=device), torch.ones(_loss_2.size(), device=device)*args.beta_param + _loss_2 - _loss_1.mean())
                    MEx_loss = torch.zeros(1).cuda()
                    # MEx routine
                    # MEx routine
                    for i in range(loss_1.shape[0]):
                        # only perform MEX, if data at location is mutual exclusive
                        if source_mex[i] == True and _source_mex[i] == True:

                            #eps = 0.0001
                            cexp_11 = torch.exp(-loss_1[i])
                            cexp_12 = torch.exp(-loss_2[i])

                            cexp_21 = torch.exp(-_loss_1[i])
                            cexp_22 = torch.exp(-_loss_2[i])

                            joint_exp_1 = (cexp_11 + cexp_12)
                            joint_exp_2 = (cexp_21 + cexp_22)

                            eps = 0.005

                            term_1 = cexp_11/joint_exp_1 * cexp_21/joint_exp_2 * \
                                (1. - (1.-cexp_11/joint_exp_1)
                                 * (1.-cexp_21/joint_exp_2))

                            term_2 = cexp_12/joint_exp_1 * cexp_22/joint_exp_2 * \
                                (1. - (1.-cexp_12/joint_exp_1)
                                 * (1.-cexp_22/joint_exp_2))

                            MEx_loss += -1.*((term_1) + (term_2))

                            if torch.isnan(term_1) or torch.isinf(term_1) or torch.isnan(term_2) or torch.isinf(term_2):
                                logger.error("NaN or Inf")

                                exit(1)

               
                # Contrastive Margin (CM)
                loss = args.alpha_param * torch.max(torch.zeros(loss_1.size(), device=device), torch.ones(loss_1.size(), device=device)*args.beta_param + loss_1 - loss_2.mean(
                )) + args.alpha_param * torch.max(torch.zeros(loss_2.size(), device=device), torch.ones(loss_2.size(), device=device)*args.beta_param + loss_2 - loss_1.mean())

                loss += args.alpha_param * torch.max(torch.zeros(_loss_1.size(), device=device), torch.ones(_loss_1.size(), device=device)*args.beta_param + _loss_1 - _loss_2.mean(
                )) + args.alpha_param * torch.max(torch.zeros(_loss_2.size(), device=device), torch.ones(_loss_2.size(), device=device)*args.beta_param + _loss_2 - _loss_1.mean())

                # Attention Mutual Exclusivity (AMEX)
                loss +=  args.lambda_param*att_reg
               
                tr_accuracy += len(np.where(loss_1.detach().cpu().numpy() -
                                            loss_2.detach().cpu().numpy() < 0.0)[0])
                if args.n_gpu > 1:
                    loss = loss.mean()  # mean() to average on multi-gpu.
                if args.gradient_accumulation_steps > 1:
                    loss = loss / args.gradient_accumulation_steps

                if args.fp16:
                    with amp.scale_loss(loss, optimizer) as scaled_loss:
                        scaled_loss.backward()
                        torch.nn.utils.clip_grad_norm_(
                            amp.master_params(optimizer), args.max_grad_norm)
                else:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(
                        model.parameters(), args.max_grad_norm)

                tr_loss += loss.item()
                nb_tr_examples += input_ids_1.size(0)
                nb_tr_steps += 1
                if (step + 1) % args.gradient_accumulation_steps == 0:
                    optimizer.step()
                    model.zero_grad()
                    scheduler.step()
                    global_step += 1
                # testing during an epoch
                if not (task_name in ["wscr", "gap", "dpr", "all"]) and global_step % 50 == 0 and (step + 1) % args.gradient_accumulation_steps == 0:
                    acc = test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss /
                               nb_tr_steps if nb_tr_steps > 0 else 0, test_set=validation_name, verbose=True)
                    logger.info("{}\t{}\n".format(nb_tr_steps, acc))
                    model.train()
                    try:  # If several processes are running in parallel this avoids overwriting results.
                        if args.ignore_bestacc:
                            updated_accuracy = 0
                        else:
                            updated_accuracy = float(
                                list(open(os.path.join(args.output_dir, "best_accuracy.txt"), 'r'))[0])
                    except:
                        updated_accuracy = 0
                    best_accuracy = max(best_accuracy, updated_accuracy)
                    #print(str(best_accuracy)+" vs. "+str(acc))
                    if acc > best_accuracy:
                        wandb.config.update(
                            {'best_accuracy': acc}, allow_val_change=True)
                        wandb.log({'best_accuracy': acc})
                        best_accuracy = acc
                        model_to_save = model.module if hasattr(
                            model, 'module') else model
                        model_to_save.save_pretrained(args.output_dir)
                        tokenizer.save_pretrained(args.output_dir)
                        torch.save(args, os.path.join(
                            args.output_dir, 'training_args.bin'))

                        with open(os.path.join(args.output_dir, "best_config.txt"), 'w') as f1_report:
                            f1_report.write("{}".format(
                                ' '.join(sys.argv[1:])))
                        with open(os.path.join(args.output_dir, "best_accuracy.txt"), 'w') as f1_report:
                            f1_report.write("{}".format(best_accuracy))
            if validation_name == "all":
                acc = (test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss/nb_tr_steps if nb_tr_steps > 0 else 0, test_set="gap-dev", verbose=True) +
                       test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss/nb_tr_steps if nb_tr_steps > 0 else 0, test_set="winobias-dev", verbose=True))/2
            else:
                acc = test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss /
                           nb_tr_steps if nb_tr_steps > 0 else 0, test_set=validation_name, verbose=True)
            logger.info("{}\t{}\n".format(nb_tr_steps, acc))
            wandb.log({'epoch': it, 'accuracy': acc, "steps": nb_tr_steps})
            model.train()
            try:

                if args.ignore_bestacc:
                    updated_accuracy = 0
                else:
                    updated_accuracy = float(
                        list(open(os.path.join(args.output_dir, "best_accuracy.txt"), 'r'))[0])
            except:
                updated_accuracy = 0
            best_accuracy = max(best_accuracy, updated_accuracy)

            if it >= 3 and acc < best_accuracy*.85:
                second_chance += 1

                if second_chance >= 3:
                    logger.info("Aborting run - not promising ...")
                    break

            if acc >= best_accuracy*.85:
                second_chance = 0

            if acc > best_accuracy:
                wandb.config.update({'best_accuracy': acc},
                                    allow_val_change=True)
                wandb.log({'best_accuracy': acc})
                best_accuracy = acc
                model_to_save = model.module if hasattr(
                    model, 'module') else model
                #torch.save(model_to_save.state_dict(), os.path.join(args.output_dir, "best_model"))
                model_to_save = model.module if hasattr(
                    model, 'module') else model
                model_to_save.save_pretrained(args.output_dir)
                tokenizer.save_pretrained(args.output_dir)
                torch.save(args, os.path.join(
                    args.output_dir, 'training_args.bin'))
                with open(os.path.join(args.output_dir, "best_accuracy.txt"), 'w') as f1_report:
                    f1_report.write("{}".format(best_accuracy))
        #reload the best model
        logger.info("Best dev acc {}".format(best_accuracy))
        model = RobertaForMaskedLM.from_pretrained(args.output_dir,
                                                   cache_dir=PYTORCH_PRETRAINED_BERT_CACHE + 'distributed_{}'.format(args.local_rank), output_attentions=True)
        model.to(device)

    if args.do_eval and (args.local_rank == -1 or torch.distributed.get_rank() == 0):
        if True:
            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="knowref-test")
            print("Knowref-test: ", res)
            wandb.log({'KnowRef': res})

            res = test(processor, args, tokenizer, model, device,
                       global_step=global_step, tr_loss=tr_loss/nb_tr_steps, test_set="gap-test")
            print("GAP-test: ", res)
            wandb.log({'GAP-test': res})

            res = test(processor, args, tokenizer, model, device,
                       global_step=global_step, tr_loss=tr_loss/nb_tr_steps, test_set="dpr-test")
            print("DPR/WSCR-test: ", res)
            wandb.log({'DPR/WSCR-test': res})

            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="wsc",  output_file='wsc-eval.tsv')
            print("WSC: ", res)
            wandb.log({'WSC': res})

        if True:
            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="winogender")
            print("WinoGender: ", res)
            wandb.log({'WinoGender': res})

            #_=test(processor, args, tokenizer, model, device, global_step = global_step, tr_loss = tr_loss/nb_tr_steps, test_set="wnli")

            res = test(processor, args, tokenizer, model, device,
                       global_step=global_step, tr_loss=tr_loss/nb_tr_steps, test_set="pdp")
            print("PDP: ", res)
            wandb.log({'PDP': res})

            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="winobias-anti1")
            print("WinoBias Anti Stereotyped Type 1: ", res)
            wandb.log({'WinoBias Anti Stereotyped Type 1': res})

            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="winobias-pro1")
            print("WinoBias Pro Stereotyped Type 1: ", res)
            wandb.log({'WinoBias Pro Stereotyped Type 1': res})

            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="winobias-anti2")
            print("WinoBias Anti Stereotyped Type 2: ", res)
            wandb.log({'WinoBias Anti Stereotyped Type 2': res})

            res = test(processor, args, tokenizer, model, device, global_step=global_step,
                       tr_loss=tr_loss/nb_tr_steps, test_set="winobias-pro2")
            print("WinoBias Pro Stereotyped Type 2: ", res)
            wandb.log({'WinoBias Pro Stereotyped Type 2': res})

        #print("Winogrande (test): ",test(processor, args, tokenizer, model, device, global_step = global_step, tr_loss = tr_loss/nb_tr_steps, test_set="winogrande-test", output_file='winogrande-test-eval.tsv'))
            res = test(processor, args, tokenizer, model, device, global_step=global_step, tr_loss=tr_loss /
                       nb_tr_steps, test_set="winogrande-dev", output_file='winogrande-dev-eval.tsv')
            print("Winogrande (dev): ", res)
            wandb.log({'Winogrande-dev': res})


if __name__ == "__main__":
    main()
