#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
This code is based on the tutorial by Sean Robertson <https://github.com/spro/practical-pytorch> found here:
https://pytorch.org/tutorials/intermediate/seq2seq_translation_tutorial.html

Students *MAY NOT* view the above tutorial or use it as a reference in any way. 
"""


from __future__ import unicode_literals, print_function, division

import argparse
import logging
import random
import time
from io import open

import matplotlib
#if you are running on the gradx/ugradx/ another cluster, 
#you will need the following line
#if you run on a local machine, you can comment it out
#matplotlib.use('agg') 
import matplotlib.pyplot as plt
import matplotlib.ticker as ticker
import torch
import torch.nn as nn
import torch.nn.functional as F
from nltk.translate.bleu_score import corpus_bleu
from torch import optim, unsqueeze
import math
import numpy as np 


logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)s %(message)s')

# we are forcing the use of cpu, if you have access to a gpu, you can set the flag to "cuda"
# make sure you are very careful if you are using a gpu on a shared cluster/grid, 
# it can be very easy to confict with other people's jobs.
device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
# device = torch.device("cpu")

SOS_token = "<SOS>"
EOS_token = "<EOS>"

SOS_index = 0
EOS_index = 1
MAX_LENGTH = 15


class Vocab:
    """ This class handles the mapping between the words and their indicies
    """
    def __init__(self, lang_code):
        self.lang_code = lang_code
        self.word2index = {}
        self.word2count = {}
        self.index2word = {SOS_index: SOS_token, EOS_index: EOS_token}
        self.n_words = 2  # Count SOS and EOS

    def add_sentence(self, sentence):
        for word in sentence.split(' '):
            self._add_word(word)

    def _add_word(self, word):
        if word not in self.word2index:
            self.word2index[word] = self.n_words
            self.word2count[word] = 1
            self.index2word[self.n_words] = word
            self.n_words += 1
        else:
            self.word2count[word] += 1


######################################################################


def split_lines(input_file):
    """split a file like:
    first src sentence|||first tgt sentence
    second src sentence|||second tgt sentence
    into a list of things like
    [("first src sentence", "first tgt sentence"), 
     ("second src sentence", "second tgt sentence")]
    """
    logging.info("Reading lines of %s...", input_file)
    # Read the file and split into lines
    lines = open(input_file, encoding='utf-8').read().strip().split('\n')
    # Split every line into pairs
    pairs = [l.split('|||') for l in lines]
    return pairs


def make_vocabs(src_lang_code, tgt_lang_code, train_file):
    """ Creates the vocabs for each of the langues based on the training corpus.
    """
    src_vocab = Vocab(src_lang_code)
    tgt_vocab = Vocab(tgt_lang_code)

    train_pairs = split_lines(train_file)

    for pair in train_pairs:
        src_vocab.add_sentence(pair[0])
        tgt_vocab.add_sentence(pair[1])

    logging.info('%s (src) vocab size: %s', src_vocab.lang_code, src_vocab.n_words)
    logging.info('%s (tgt) vocab size: %s', tgt_vocab.lang_code, tgt_vocab.n_words)

    return src_vocab, tgt_vocab

######################################################################

def tensor_from_sentence(vocab, sentence):
    """creates a tensor from a raw sentence
    """
    indexes = []
    for word in sentence.split():
        try:
            indexes.append(vocab.word2index[word])
        except KeyError:
            pass
            # logging.warn('skipping unknown subword %s. Joint BPE can produces subwords at test time which are not in vocab. As long as this doesnt happen every sentence, this is fine.', word)
    indexes.append(EOS_index)
    return torch.tensor(indexes, dtype=torch.long, device=device).view(-1, 1)


def tensors_from_pair(src_vocab, tgt_vocab, pair):
    """creates a tensor from a raw sentence pair
    """
    input_tensor = tensor_from_sentence(src_vocab, pair[0])
    target_tensor = tensor_from_sentence(tgt_vocab, pair[1])
    return input_tensor, target_tensor


######################################################################

class LSTMBlock(nn.Module):
    """A single LSTM block implementation."""


    def __init__(self, input_dim, hidden_dim, use_bias=True):
        super(LSTMBlock, self).__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim

        self.wx = nn.Linear(hidden_dim*2, hidden_dim * 4, bias=use_bias)
        self.wh = nn.Linear(hidden_dim*2, hidden_dim * 4, bias=use_bias)
        self.init_weights()

    def forward(self, input, hidden):
        if type(hidden) is not tuple:
            h_prev, c_prev = (
                torch.zeros(1, self.hidden_dim).to(input.device),
                torch.zeros(1, self.hidden_dim).to(input.device),
            )
        else:
            h_prev, c_prev = hidden
        print(input.shape, h_prev.shape)
        all_gates = self.wx(input) + self.wh(h_prev)
        print(all_gates.shape)
        input_gate, forget_gate, cell_gate, output_gate = torch.chunk(all_gates, 4, 1)

        input_gate = torch.sigmoid(input_gate)
        forget_gate = torch.sigmoid(forget_gate)
        output_gate = torch.sigmoid(output_gate)
        cell_gate = torch.tanh(cell_gate)

        c_curr = forget_gate * c_prev + input_gate * cell_gate
        h_curr = output_gate * torch.tanh(c_curr)

        return h_curr, c_curr

    def init_weights(self):
        s = 1.0 / math.sqrt(self.hidden_dim)
        for weight in self.parameters():
            weight.data.uniform_(-s, s)

class EncoderRNN(nn.Module):
    """the class for the encoder RNN
    """
    def __init__(self, input_size, hidden_size):
        super(EncoderRNN, self).__init__()
        self.hidden_size = hidden_size
        """Initilize a word embedding and bi-directional LSTM encoder
        For this assignment, you should *NOT* use nn.LSTM. 
        Instead, you should implement the equations yourself.
        See, for example, https://en.wikipedia.org/wiki/Long_short-term_memory#LSTM_with_a_forget_gate
        You should make your LSTM modular and re-use it in the Decoder.
        """
        self.embedding = nn.Embedding(input_size, hidden_size)
        self.forward_lstm = LSTMBlock(hidden_size, hidden_size)
        self.backward_lstm = LSTMBlock(hidden_size, hidden_size)


    def forward(self, input, hidden):
        """runs the forward pass of the encoder
        returns the output and the hidden state
        """
        embedded = self.embedding(input)

        forward_states = hidden
        backward_states = hidden

        all_forward_hiddens = []
        all_backward_hiddens = []

        for x in embedded:
            forward_h, forward_c = self.forward_lstm(x, forward_states)
            all_forward_hiddens.append(forward_h)
            forward_states = (forward_h, forward_c)

        for x in reversed(embedded):
            backward_h, backward_c = self.backward_lstm(x, backward_states)
            all_backward_hiddens.append(backward_h)
            backward_states = (backward_h, backward_c)

        all_backward_hiddens = list(reversed(all_backward_hiddens))
        encoder_hiddens = torch.cat([torch.cat([f, b], dim=-1) for f, b in zip(all_forward_hiddens, all_backward_hiddens)], dim=1)

        return encoder_hiddens

    def get_initial_hidden_state(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)

class AttnDecoderRNN(nn.Module):
    """the class for the decoder 
    """
    def __init__(self, hidden_size, output_size, dropout_p=0.1, max_length=MAX_LENGTH):
        """Initilize your word embedding, decoder LSTM, and weights needed for your attention here
        """
        super(AttnDecoderRNN, self).__init__()
        self.hidden_size = hidden_size
        self.output_size = output_size
        self.dropout_p = dropout_p
        self.max_length = max_length

        self.emb = nn.Embedding(self.output_size, 2*self.hidden_size)
        self.dropout = nn.Dropout(self.dropout_p)
        self.attn = nn.Linear(4 * self.hidden_size, 2 * self.max_length)
        self.attn_layers = nn.Linear(4 * self.hidden_size, 2 * self.hidden_size)
        self.out = nn.Linear(self.hidden_size, self.output_size)
        self.lstm = LSTMBlock(self.output_size, self.hidden_size)


    def forward(self, input, hidden, encoder_outputs):
        """runs the forward pass of the decoder
        returns the log_softmax, hidden state, and attn_weights
        Dropout (self.dropout) should be applied to the word embeddings.
        """
        embedding = self.emb(input).view(1, 1, -1)
        embedding = self.dropout(embedding)
        attn_weight = None
        context = None
        print(embedding.shape, hidden.shape)
        if hidden[0].dim() == 2:
            attn_weight = F.softmax(self.attn(torch.cat((embedding[0], hidden[0]), 1)), dim=1)
            print(attn_weight.shape, encoder_outputs.shape)
            context = torch.matmul(attn_weight, encoder_outputs.repeat(2, 2)).unsqueeze(0)
        else:
            attn_weight = F.softmax(self.attn(torch.cat((embedding[0], hidden[0][0]), 1)), dim=1)
            print(attn_weight.shape, encoder_outputs.shape)
            context = torch.matmul(attn_weight.unsqueeze(0), encoder_outputs.repeat(2, 2).unsqueeze(0))
        print(embedding[0].shape, context[0].shape)
        print(torch.cat((embedding[0], context[0]), 1).shape)
        output = self.attn_layers(torch.cat((embedding[0], context[0]), 1)).unsqueeze(0)
        print(output.shape)
        output, h_state = self.lstm.forward(output, hidden[0])
        log_softmax = F.log_softmax(self.out(output[0]), dim=1)

        return log_softmax, h_state, attn_weights

    def get_initial_hidden_state(self):
        return torch.zeros(1, 1, self.hidden_size, device=device)


######################################################################

def train(input_tensor, target_tensor, encoder, decoder, optimizer, criterion, max_length=MAX_LENGTH):
    encoder_hidden = encoder.get_initial_hidden_state()
    print(f'{encoder_hidden} boooooof')

    # make sure the encoder and decoder are in training mode so dropout is applied
    encoder.train()
    decoder.train()
    optimizer.zero_grad()

    encoder_outputs = torch.zeros(max_length, encoder.hidden_size, device=device)

    for i in range(input_tensor.shape[0]):
        encoder_hidden = encoder(input_tensor[i].repeat(2,1), encoder_hidden)
        encoder_outputs[i] = encoder_hidden[0][0]


    decoder_input = torch.tensor([[SOS_index]], device=device)

    decoder_input = torch.tensor([[0]], device=device)
    loss = 0

    for idx in range(target_tensor.shape[0]):
        # print(decoder_input.shape, decoder_hidden.shape, encoder_outputs.shape)
        logits, decoder_hidden, _ = decoder(decoder_input, encoder_hidden[0].resize(1,1,512), encoder_outputs)
        logits = torch.where(logits[0] == max(logits[0]))[0]
        squeezed_logits = logits.squeeze().detach()

        current_loss = criterion(squeezed_logits, target_tensor[idx])
        total_loss += current_loss
        if np.array(decoder_input) == "<EOS>": # if sentence completed stop trainingxa
            break

    loss.backward()
    optimizer.step()

    return loss.item()

######################################################################

def translate(encoder, decoder, sentence, src_vocab, tgt_vocab, max_length=MAX_LENGTH):
    """
    runs tranlsation, returns the output and attention
    """

    # switch the encoder and decoder to eval mode so they are not applying dropout
    encoder.eval()
    decoder.eval()

    with torch.no_grad():
        input_tensor = tensor_from_sentence(src_vocab, sentence)
        input_length = input_tensor.size()[0]
        encoder_hidden = (encoder.get_initial_hidden_state(), encoder.get_initial_hidden_state())

        encoder_outputs = torch.zeros(max_length, encoder.hidden_size*2, device=device)

        raw_encoder_output = encoder(input_tensor, encoder_hidden)
        encoder_output_simplified = torch.squeeze(raw_encoder_output)

        for ei in range(input_length):
            current_encoder_output = encoder_output_simplified[ei, :]
            encoder_outputs[ei] = current_encoder_output

        decoder_input = torch.tensor([[SOS_index]], device=device)

        encoder_output_dim = encoder_output_simplified.shape[1]
        half_dim = encoder_output_dim // 2
        decoder_hidden_initial = encoder_output_simplified[0, half_dim:]
        decoder_hidden = torch.unsqueeze(decoder_hidden_initial, 0)

        decoded_words = []
        decoder_attentions = torch.zeros(max_length, max_length)

        for di in range(max_length):
            decoder_output, decoder_hidden, decoder_attention = decoder(
                decoder_input, decoder_hidden, encoder_outputs)
            decoder_attentions[di] = decoder_attention.data
            _, topi = decoder_output.data.topk(1)
            if topi.item() == EOS_index:
                decoded_words.append(EOS_token)
                break
            else:
                decoded_words.append(tgt_vocab.index2word[topi.item()])

            decoder_input = topi.squeeze().detach()

        return decoded_words, decoder_attentions[:di + 1]


######################################################################

# Translate (dev/test)set takes in a list of sentences and writes out their transaltes
def translate_sentences(encoder, decoder, pairs, src_vocab, tgt_vocab, max_num_sentences=None, max_length=MAX_LENGTH):
    output_sentences = []
    for pair in pairs[:max_num_sentences]:
        output_words, attentions = translate(encoder, decoder, pair[0], src_vocab, tgt_vocab)
        output_sentence = ' '.join(output_words)
        output_sentences.append(output_sentence)
    return output_sentences


######################################################################
# We can translate random sentences  and print out the
# input, target, and output to make some subjective quality judgements:
#

def translate_random_sentence(encoder, decoder, pairs, src_vocab, tgt_vocab, n=1):
    for i in range(n):
        pair = random.choice(pairs)
        print('>', pair[0])
        print('=', pair[1])
        output_words, attentions = translate(encoder, decoder, pair[0], src_vocab, tgt_vocab)
        output_sentence = ' '.join(output_words)
        print('<', output_sentence)
        print('')


######################################################################

def show_attention(input_sentence, output_words, attentions):
    """visualize the attention mechanism. And save it to a file. 
    Plots should look roughly like this: https://i.stack.imgur.com/PhtQi.png
    You plots should include axis labels and a legend.
    you may want to use matplotlib.
    """
    fig = plt.figure()

    input_sentence_split = input_sentence.split() + ['<EOS>']
    output_words_split = output_words

    attentions_np = attentions.numpy()[:,:len(input_sentence_split)]
    ax = fig.add_subplot(1, 1, 1)
    im = ax.matshow(attentions_np, cmap='gray')

    ax.set_xticks(range(len(input_sentence_split)))
    ax.set_yticks(range(len(output_words_split)))

    ax.set_xticklabels(input_sentence_split, rotation=90)
    ax.set_yticklabels(output_words_split)
    
    plt.tight_layout()
    plt.savefig('plots/'+input_sentence+'.png')


def translate_and_show_attention(input_sentence, encoder1, decoder1, src_vocab, tgt_vocab):
    output_words, attentions = translate(
        encoder1, decoder1, input_sentence, src_vocab, tgt_vocab)
    print('input =', input_sentence)
    print('output =', ' '.join(output_words))
    show_attention(input_sentence, output_words, attentions)


def clean(strx):
    """
    input: string with bpe, EOS
    output: list without bpe, EOS
    """
    return ' '.join(strx.replace('@@ ', '').replace(EOS_token, '').strip().split())


######################################################################

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--hidden_size', default=256, type=int,
                    help='hidden size of encoder/decoder, also word vector size')
    ap.add_argument('--n_iters', default=100000, type=int,
                    help='total number of examples to train on')
    ap.add_argument('--print_every', default=5000, type=int,
                    help='print loss info every this many training examples')
    ap.add_argument('--checkpoint_every', default=10000, type=int,
                    help='write out checkpoint every this many training examples')
    ap.add_argument('--initial_learning_rate', default=0.001, type=int,
                    help='initial learning rate')
    ap.add_argument('--src_lang', default='fr',
                    help='Source (input) language code, e.g. "fr"')
    ap.add_argument('--tgt_lang', default='en',
                    help='Source (input) language code, e.g. "en"')
    ap.add_argument('--train_file', default='data/fren.train.bpe',
                    help='training file. each line should have a source sentence,' +
                         'followed by "|||", followed by a target sentence')
    ap.add_argument('--dev_file', default='data/fren.dev.bpe',
                    help='dev file. each line should have a source sentence,' +
                         'followed by "|||", followed by a target sentence')
    ap.add_argument('--test_file', default='data/fren.test.bpe',
                    help='test file. each line should have a source sentence,' +
                         'followed by "|||", followed by a target sentence' +
                         ' (for test, target is ignored)')
    ap.add_argument('--out_file', default='out.txt',
                    help='output file for test translations')
    ap.add_argument('--load_checkpoint', nargs=1,
                    help='checkpoint file to start from')

    args = ap.parse_args()

    # process the training, dev, test files

    # Create vocab from training data, or load if checkpointed
    # also set iteration 
    if args.load_checkpoint is not None:
        state = torch.load(args.load_checkpoint[0])
        iter_num = state['iter_num']
        src_vocab = state['src_vocab']
        tgt_vocab = state['tgt_vocab']
    else:
        iter_num = 0
        src_vocab, tgt_vocab = make_vocabs(args.src_lang,
                                           args.tgt_lang,
                                           args.train_file)

    encoder = EncoderRNN(src_vocab.n_words, args.hidden_size).to(device)
    decoder = AttnDecoderRNN(args.hidden_size, tgt_vocab.n_words, dropout_p=0.1).to(device)

    # encoder/decoder weights are randomly initilized
    # if checkpointed, load saved weights
    if args.load_checkpoint is not None:
        encoder.load_state_dict(state['enc_state'])
        decoder.load_state_dict(state['dec_state'])

    # read in datafiles
    train_pairs = split_lines(args.train_file)
    dev_pairs = split_lines(args.dev_file)
    test_pairs = split_lines(args.test_file)

    # set up optimization/loss
    params = list(encoder.parameters()) + list(decoder.parameters())  # .parameters() returns generator
    optimizer = optim.Adam(params, lr=args.initial_learning_rate)
    criterion = nn.NLLLoss()

    # optimizer may have state
    # if checkpointed, load saved state
    if args.load_checkpoint is not None:
        optimizer.load_state_dict(state['opt_state'])

    start = time.time()
    print_loss_total = 0  # Reset every args.print_every

    while iter_num < args.n_iters:
        iter_num += 1
        training_pair = tensors_from_pair(src_vocab, tgt_vocab, random.choice(train_pairs))
        input_tensor = training_pair[0]
        target_tensor = training_pair[1]
        loss = train(input_tensor, target_tensor, encoder,
                     decoder, optimizer, criterion)
        print_loss_total += loss

        if iter_num % args.checkpoint_every == 0:
            state = {'iter_num': iter_num,
                     'enc_state': encoder.state_dict(),
                     'dec_state': decoder.state_dict(),
                     'opt_state': optimizer.state_dict(),
                     'src_vocab': src_vocab,
                     'tgt_vocab': tgt_vocab,
                     }
            filename = 'state_%010d.pt' % iter_num
            torch.save(state, filename)
            logging.debug('wrote checkpoint to %s', filename)

        if iter_num % args.print_every == 0:
            print_loss_avg = print_loss_total / args.print_every
            print_loss_total = 0
            logging.debug('time since start:%s (iter:%d iter/n_iters:%d%%) loss_avg:%.4f',
                         time.time() - start,
                         iter_num,
                         iter_num / args.n_iters * 100,
                         print_loss_avg)
            # translate from the dev set
            translate_random_sentence(encoder, decoder, dev_pairs, src_vocab, tgt_vocab, n=2)
            translated_sentences = translate_sentences(encoder, decoder, dev_pairs, src_vocab, tgt_vocab)

            references = [[clean(pair[1]).split(), ] for pair in dev_pairs[:len(translated_sentences)]]
            candidates = [clean(sent).split() for sent in translated_sentences]
            dev_bleu = corpus_bleu(references, candidates)
            logging.debug('Dev BLEU score: %.2f', dev_bleu)

    # translate test set and write to file
    translated_sentences = translate_sentences(encoder, decoder, test_pairs, src_vocab, tgt_vocab)
    with open(args.out_file, 'wt', encoding='utf-8') as outf:
        for sent in translated_sentences:
            outf.write(clean(sent) + '\n')

    # Visualizing Attention
    translate_and_show_attention("on p@@ eu@@ t me faire confiance .", encoder, decoder, src_vocab, tgt_vocab)
    translate_and_show_attention("j en suis contente .", encoder, decoder, src_vocab, tgt_vocab)
    translate_and_show_attention("vous etes tres genti@@ ls .", encoder, decoder, src_vocab, tgt_vocab)
    translate_and_show_attention("c est mon hero@@ s ", encoder, decoder, src_vocab, tgt_vocab)


if __name__ == '__main__':
    main()
