#!/usr/bin/env python
import os
scenarios = ['simple', 'simple_adversary']
num_episode = 10000

for scenario in scenarios:
    os.system('python train.py --scenario %s --num-episode %d &'
              % (scenario, num_episode))
