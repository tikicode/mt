#!usr/bin/env python
import optparse
import sys
from collections import defaultdict
import numpy as np
import model_one

def train_model_two(bitext):
    t_probs = defaultdict(lambda : 1/len(bitext))
    e_total = defaultdict(lambda: 0.0)
    t_probs = model_one.train_model_one(t_probs, bitext, 10)

    en_vocab = []
    fr_vocab = []
    for (f_sent, e_sent) in bitext:
        en_vocab.extend(e_sent)
        fr_vocab.extend(f_sent)

    en_vocab = set(en_vocab)
    fr_vocab = set(fr_vocab)

    q = defaultdict(float)
    for (f_sent, e_sent) in bitext:
        f_len = len(f_sent)
        e_len = len(e_sent)
        for i in range(f_len):
            for j in range(e_len):
                q[(i,j,f_len,e_len)] = 1/f_len
 
    for i in range(10):
        f_total = defaultdict(float)
        fe_count = defaultdict(float)
        q_count = defaultdict(lambda: 0.0)
        q_total = defaultdict(lambda: 0.0)
        for (f_sent, e_sent) in bitext:
            f_len = len(f_sent)
            e_len = len(e_sent)
            for (j, e_j) in enumerate(e_sent):
                for (i, f_i) in enumerate(f_sent):
                    e_total[e_j] += t_probs[(f_i,e_j)] * q[(i,j,f_len,e_len)] # Normalize
            
            for (j, e_j) in enumerate(e_sent):
                for (i, f_i) in enumerate(f_sent):
                    c = t_probs[(f_i,e_j)] * q[(i,j,f_len,e_len)] / e_total[e_j]
                    q_count[(i,j,f_len,e_len)] += c
                    q_total[(j,f_len,e_len)] += c
                    fe_count[(f_i,e_j)] += c
                    f_total[f_i] += c

        q = defaultdict(lambda: 0.0)
        t_probs = defaultdict(lambda: 0.0)

        # Smoothing
        for (f_sent, e_sent) in bitext:
            f_len = len(f_sent)
            e_len = len(e_sent)
            lamd = 1.0
            for (j, e_j) in enumerate(e_sent):
                for (i, f_i) in enumerate(f_sent):
                    if q_count[(i,j,f_len,e_len)] > 0 and q_count[(i,j,f_len,e_len)] < lamd:
                        lamd = q_count[(i,j,f_len,e_len)]
            
            lamd *= 0.25
            for (j, e_j) in enumerate(e_sent):
                for (i, f_i) in enumerate(f_sent):
                    q_total[(i,j,f_len,e_len)] += lamd

            init = lamd * e_len
            for (j, e_j) in enumerate(e_sent):
                q_total[(j,f_len,e_len)] += init

        for f in fr_vocab:
            for e in en_vocab:
                t_probs[(f,e)] = fe_count[(f,e)] / f_total[f]
                    
        for (f_sent, e_sent) in bitext:
            f_len = len(f_sent)
            e_len = len(e_sent)
            for (j, e_j) in enumerate(e_sent):
                for (i, f_i) in enumerate(f_sent):
                    q[(i,j,f_len,e_len)] = q_count[(i,j,f_len,e_len)] / q_total[(j,f_len,e_len)]

    return t_probs, q


if __name__ == "__main__":
    # Read in command line arguments
    optparser = optparse.OptionParser()
    optparser.add_option("-d", "--data", dest="train", default="data/hansards", help="Data filename prefix (default=data)")
    optparser.add_option("-e", "--english", dest="english", default="e", help="Suffix of English filename (default=e)")
    optparser.add_option("-f", "--french", dest="french", default="f", help="Suffix of French filename (default=f)")
    optparser.add_option("-n", "--num_sentences", dest="num_sents", default=100000000000, type="int", help="Number of sentences to use for training and alignment")
    (opts, _) = optparser.parse_args()
    f_data = "%s.%s" % (opts.train, opts.french)
    e_data = "%s.%s" % (opts.train, opts.english)

    sys.stderr.write("Training with HMM...")
    bitext = [[sentence.strip().split() for sentence in pair] for pair in zip(open(f_data), open(e_data))][:opts.num_sents]

    t_probs, q = train_model_two(bitext)

    # Alignment
    for (f, e) in bitext:
        for (i, f_i) in enumerate(f): 
            best_prob = 0
            best_j = 0
            for (j, e_j) in enumerate(e):
                if t_probs[(f_i,e_j)] > best_prob:
                    best_prob = t_probs[(f_i,e_j)]

                    best_j = j
            sys.stdout.write("%i-%i " % (i,best_j))
        sys.stdout.write("\n")