#!/bin/bash

melody_rnn_generate \
--config lookback_rnn \
--bundle_file $1 \
--num_outputs $2 \
--output_dir $3 \
--num_steps 512 \
--primer_melody "[60]"
