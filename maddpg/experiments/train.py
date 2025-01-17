import argparse
import gym
import numpy as np
import os
import tensorflow as tf
import time
import pickle

import maddpg.common.tf_util as U
from maddpg.trainer.maddpg import MADDPGAgentTrainer
import tensorflow.contrib.layers as layers

from gym.wrappers.monitoring import video_recorder as gvr
from collections import defaultdict
import os.path as osp
import pandas as pd
import string




def parse_args():
    parser = argparse.ArgumentParser("Reinforcement Learning experiments for multiagent environments")
    # Environment
    parser.add_argument("--scenario", type=str, default="simple", help="name of the scenario script")
    parser.add_argument("--max-episode-len", type=int, default=25, help="maximum episode length")
    parser.add_argument("--num-episodes", type=int, default=60000, help="number of episodes")
    parser.add_argument("--num-adversaries", type=int, default=0, help="number of adversaries")
    parser.add_argument("--good-policy", type=str, default="maddpg", help="policy for good agents")
    parser.add_argument("--adv-policy", type=str, default="maddpg", help="policy of adversaries")
    # Core training parameters
    parser.add_argument("--lr", type=float, default=1e-2, help="learning rate for Adam optimizer")
    parser.add_argument("--gamma", type=float, default=0.95, help="discount factor")
    parser.add_argument("--batch-size", type=int, default=1024, help="number of episodes to optimize at the same time")
    parser.add_argument("--num-units", type=int, default=64, help="number of units in the mlp")
    # Checkpointing
    parser.add_argument("--exp-name", type=str, default=None, help="name of the experiment")
    parser.add_argument("--save-dir", type=str, default=None, help="directory in which training state and model should be saved")
    parser.add_argument("--save-rate", type=int, default=1000, help="save model once every time this many episodes are completed")
    parser.add_argument("--load-model", type=str, default=None, help="loaded model")
    # Evaluation
    parser.add_argument("--restore", action="store_true", default=False)
    parser.add_argument("--display", action="store_true", default=False)
    parser.add_argument("--benchmark", action="store_true", default=False)
    parser.add_argument("--benchmark-iters", type=int, default=100000, help="number of iterations run for benchmarking")
    parser.add_argument("--benchmark-dir", type=str, default="./benchmark_files/", help="directory where benchmark data is saved")
    parser.add_argument("--plots-dir", type=str, default=None, help="directory where plot data is saved")
    # added by JK
    parser.add_argument("--video-record", action="store_true", default=False, help='if ture, record a video')
    parser.add_argument("--video-file-name", type=str, default=None)
    parser.add_argument("--video-frames-per-second", type=int, default=20, help='only used on the video recording')
    parser.add_argument("--display-sleep-second", type=float, default=0.01, help='only used for display')
    parser.add_argument("--dic-variable-max-episode-len", type=str, default='{}')
    parser.add_argument("--seed", type=int, default=None, help="random seed")
    return parser.parse_args()


def mlp_model(inputs, num_outputs, scope, reuse=False, num_units=64, rnn_cell=None):
    # This model takes as input an observation and returns values of all actions
    with tf.variable_scope(scope, reuse=reuse):
        out = inputs
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu)
        out = layers.fully_connected(out, num_outputs=num_units, activation_fn=tf.nn.relu)
        out = layers.fully_connected(out, num_outputs=num_outputs, activation_fn=None)
        return out


def make_env(scenario_name, arglist, benchmark=False):
    from multiagent.environment import MultiAgentEnv
    import multiagent.scenarios as scenarios

    # load scenario from script
    scenario = scenarios.load(scenario_name + ".py").Scenario()
    # create world
    world = scenario.make_world()
    # create multiagent environment
    if benchmark:
        env = MultiAgentEnv(world, scenario.reset_world, scenario.reward, scenario.observation, scenario.benchmark_data)
    else:
        env = MultiAgentEnv(world, scenario.reset_world, scenario.reward, scenario.observation)
    return env


def get_trainers(env, num_adversaries, obs_shape_n, arglist):
    trainers = []
    model = mlp_model
    trainer = MADDPGAgentTrainer
    for i in range(num_adversaries):
        trainers.append(trainer(
            "agent_%d" % i, model, obs_shape_n, env.action_space, i, arglist,
            local_q_func=(arglist.adv_policy=='ddpg')))
    for i in range(num_adversaries, env.n):
        trainers.append(trainer(
            "agent_%d" % i, model, obs_shape_n, env.action_space, i, arglist,
            local_q_func=(arglist.good_policy=='ddpg')))
    return trainers


def set_dirs(arglist):
    if arglist.display:
        return
    elif arglist.restore:
        _restore_dirs(arglist)
    else:
        _set_new_dirs(arglist)


def _restore_dirs(arglist):
    arglist.save_dir = osp.dirname(arglist.load_model)
    arglist.plots_dir = arglist.save_dir.replace('models', 'learning_curves')


def _set_new_dirs(arglist):
    exp_dir = './exp_results'
    if arglist.exp_name is None:
        arglist.exp_name = arglist.scenario + '__' + time.strftime("%Y-%m-%d_%H-%M-%S")
        if arglist.seed is not None:
            arglist.exp_name += '_seed%d' % arglist.seed
    exp_dir = osp.join(exp_dir, arglist.exp_name)

    if arglist.plots_dir is None:
        arglist.plots_dir = osp.join(exp_dir, 'learning_curves')
    if arglist.save_dir is None:
        arglist.save_dir = osp.join(exp_dir, 'models')

    for d in (arglist.plots_dir, arglist.save_dir):
        if not osp.exists(d):
            os.makedirs(d, exist_ok=True)


def set_max_episode_len(arglist):
    dic_par_var_epi_len = eval(arglist.dic_variable_max_episode_len)
    if len(dic_par_var_epi_len):
        n_episode = 0
        if arglist.restore:
            n_episode, _, _ = restore_vars(arglist.load_model)
        arglist.max_episode_len =\
            get_variable_max_episode_len(dic_par_var_epi_len, n_episode)
    return arglist.max_episode_len, dic_par_var_epi_len


def restore_vars(load_model):
    basename = osp.basename(load_model)
    last_saved_episode = int(basename.split('-')[-1])
    n_episode = last_saved_episode + 1
    df_rew = pd.read_csv(osp.join(arglist.plots_dir, 'rewards.csv'))
    train_step =\
        int(df_rew.loc[df_rew['episode'] == last_saved_episode, 'step'].values[0])
    return n_episode, train_step, last_saved_episode


def print_reward(n_episode, num_adversaries, train_step, agent_rewards,
                 episode_rewards, save_rate, t_start):
    if num_adversaries == 0:
        print("steps: {}, episodes: {}, mean episode reward: {}, time: {}".format(
            train_step, n_episode, np.mean(episode_rewards[-save_rate:]), round(time.time()-t_start, 3)))
    else:
        print("steps: {}, episodes: {}, mean episode reward: {}, agent episode reward: {}, time: {}".format(
            train_step, n_episode, np.mean(episode_rewards[-save_rate:]),
            [np.mean(rew[-save_rate:]) for rew in agent_rewards], round(time.time()-t_start, 3)))


def save_model(saver, arglist, episode_rewards, n_episode):
    saved_model = osp.join(arglist.save_dir, 'model-%d' % n_episode)
    U.save_state(saved_model, saver=saver)


def save_curves(n_episode, train_step,
                final_ep_reward, final_ep_ag_reward, arglist):
    rew_file_name = osp.join(arglist.plots_dir, 'rewards.csv')
    agrew_file_name = osp.join(arglist.plots_dir, 'agents_rewards.csv')
    is_first_save = False
    if not osp.exists(rew_file_name):
        is_first_save = True

    with open(rew_file_name, 'a') as g:
        if is_first_save:
            g.write('episode,step,total_reward\n')
        # for i, v in enumerate(final_ep_rewards, 1):
        g.write('%d, %d, %f\n' % (n_episode, train_step, final_ep_reward))

    with open(agrew_file_name, 'a') as g:
        n_agents = len(final_ep_ag_reward)
        if is_first_save:
            agent_names = ['agent%d_rew' % i for i in range(n_agents)]
            header = ('{},' * (n_agents + 2))\
                .format('episode', 'step', *(agent_names)).rstrip(',')
            g.write(header + '\n')
        # for i, v in enumerate(final_ep_ag_rewards, 1):
        g.write(('{}, ' * (n_agents + 2)).format(n_episode, train_step,
                                                 *final_ep_ag_reward).rstrip(', ') + '\n')


def save_states(n_episode, train_step, states_history, arglist):
    def calc_states(states_history):
        state_names =\
            ['agent{i}_energy_min', 'agent{i}_attention_min']
        rt_states = []
        for each_agent_states in states_history:
            val_history = []
            for epi_states in each_agent_states[-arglist.save_rate:]:
                epi_states = np.array(epi_states)
                # vals = [min(epi_ene), max(epi_ene), np.mean(epi_ene)]
                vals = [min(epi_states[:, 0]), min(epi_states[:, 1])]
                val_history.append(vals)
            recent_val_avg = np.array(val_history).mean(axis=0)
            rt_states.append(recent_val_avg)
        return rt_states, state_names

    ag_states_file_name = osp.join(arglist.plots_dir, 'agents_states.csv')
    is_first_save = False
    if not osp.exists(ag_states_file_name):
        is_first_save = True

    state_vals, state_names = calc_states(states_history)
    with open(ag_states_file_name, 'a') as g:
        if is_first_save:
            g.write('episode,step')
            for i in range(len(state_vals)):
                g.write(',')
                g.write(','.join(state_names).format(i=i))
            g.write('\n')

        g.write('%d, %d' % (n_episode, train_step))
        for each_agent_state in state_vals:
            g.write((', {}' * len(state_names)).format(*each_agent_state))
        g.write('\n')

        for i, each_agent_state in enumerate(state_vals):
            print(', '.join(state_names).format(i=i))
            print(each_agent_state)


def save_actions(action_history, video_file_name):
    for i, dic_each_agent_actions in enumerate(action_history):
        outfile = video_file_name.replace('.mp4', '_actions_agent%d.csv' % i)
        df_out = _dic_to_df(dic_each_agent_actions)
        rename_map = dict([(i, 'act%d' % i) for i in range(len(df_out.columns) - 2)])
        df_out.rename(columns=rename_map, inplace=True)
        df_out.to_csv(outfile, index=False)


def save_every_state(states_history_display, video_file_name):
    state_keys = {0: 'energy', 1: 'attention'}
    for i, dic_each_agent_energy in enumerate(states_history_display):
        outfile = video_file_name.replace('.mp4', '_states_agent%d.csv' % i)
        df_out = _dic_to_df(dic_each_agent_energy)
        rename_map = state_keys
        df_out.rename(columns=rename_map, inplace=True)
        df_out.to_csv(outfile, index=False)


def save_messages(dic_messages, video_file_name):
    outfile = video_file_name.replace('.mp4', '_messages.csv')
    df_out = _dic_to_df(dic_messages)
    df_out.to_csv(outfile, index=False)


def _dic_to_df(dic):
    df_out = []
    for n_epi, contents in dic.items():
        df_each_epi = pd.DataFrame(contents)
        df_each_epi['episode'] = n_epi
        df_each_epi['step'] = np.arange(len(df_each_epi))
        df_out.append(df_each_epi)
    df_out = pd.concat(df_out, ignore_index=True)
    return df_out


def get_variable_max_episode_len(dic, n_episode=0):
    max_episode_len = dic['min_max_episode_len']\
        * np.power(2, n_episode / dic['twice_episodes'])
    return int(min(max_episode_len, dic['max_max_episode_len']))


def get_messages(agents):
    def rename(agent_name):
        return agent_name.replace('agent ', 'a')

    dic_message_step = {}
    for agent in agents:
        for other in agents:
            if other is agent:
                    continue
            if np.all(other.state.c == 0):
                word = '_'
            else:
                word = string.ascii_uppercase[np.argmax(other.state.c)]
            key = '%s_to_%s' % (rename(other.name), rename(agent.name))
            dic_message_step[key] = word
    if len(dic_message_step) > 0:
        print(str(dic_message_step).replace("'", "").strip('{}'))
        return dic_message_step


def set_random_seed(env, seed):
    import random
    if seed is None:
        return
    random.seed(seed)
    np.random.seed(seed)
    tf.set_random_seed(seed)
    env.seed(seed)


def is_trade(env):
    return np.any([agent.trade for agent in env.agents])


def train(arglist):
    set_dirs(arglist)
    max_episode_len, dic_par_var_epi_len = set_max_episode_len(arglist)
    # Create environment
    env = make_env(arglist.scenario, arglist, arglist.benchmark)
    set_random_seed(env, arglist.seed)
    # with U.single_threaded_session():
    with tf.Session():
        # env = make_env(arglist.scenario, arglist, arglist.benchmark)
        # Create agent trainers
        obs_shape_n = [env.observation_space[i].shape for i in range(env.n)]
        num_adversaries = min(env.n, arglist.num_adversaries)
        trainers = get_trainers(env, num_adversaries, obs_shape_n, arglist)
        print('Using good policy {} and adv policy {}'
              .format(arglist.good_policy, arglist.adv_policy))

        # Initialize
        U.initialize()

        if arglist.display or arglist.restore or arglist.benchmark:
            # print('Loading previous state...')
            print('Loading %s...' % arglist.load_model)
            U.load_state(arglist.load_model)

        episode_rewards = [0.0]  # sum of rewards for all agents
        agent_rewards = [[0.0] for _ in range(env.n)]  # individual agent reward
        final_ep_rewards = []  # sum of rewards for training curve
        final_ep_ag_rewards = []  # agent rewards for training curve
        agent_info = [[[]]]  # placeholder for benchmarking info
        saver = tf.train.Saver(max_to_keep=None)
        obs_n = env.reset()
        n_episode = 1
        episode_step = 0
        train_step = 0
        last_saved_episode = -1
        dic_messages = defaultdict(list)  # for evaluation
        action_history = [defaultdict(list) for _ in range(env.n)]  # for evaluation
        t_start = t_start0 = time.time()
        states_episode = [[] for _ in range(env.n)]
        states_history = [[] for _ in range(env.n)]
        states_history_display =\
            [defaultdict(list) for _ in range(env.n)]  # for evaluation

        # restore some variables of the restored model
        if arglist.restore:
            n_episode, train_step, last_saved_episode =\
                restore_vars(arglist.load_model)
        n_episode0 = n_episode
        if arglist.video_record:
            env.metadata['video.frames_per_second'] = arglist.video_frames_per_second
            recorder = gvr.VideoRecorder(env, arglist.video_file_name, enabled=True)
        if arglist.exp_name is not None:
            print('Starting iterations of %s...' % arglist.exp_name)

        while True:
            # get action
            action_n = [agent.action(obs) for agent, obs in zip(trainers, obs_n)]
            # environment step
            new_obs_n, rew_n, done_n, info_n = env.step(action_n)
            episode_step += 1
            done = all(done_n)
            terminal = (episode_step >= max_episode_len)
            # collect experience
            for i, agent in enumerate(trainers):
                agent.experience(obs_n[i], action_n[i], rew_n[i],
                                 new_obs_n[i], done_n[i], terminal)
            obs_n = new_obs_n

            for i, rew in enumerate(rew_n):
                episode_rewards[-1] += rew
                agent_rewards[i][-1] += rew

            if is_trade(env):
                for i, agent in enumerate(env.agents):
                    states = (agent.state.energy, agent.state.attention)
                    states_episode[i].append(states)

            if arglist.display:
                for i, act in enumerate(action_n):
                    action_history[i][n_episode].append(list(act))
                    if is_trade(env):
                        states = [env.agents[i].state.energy,
                                  env.agents[i].state.attention]
                        states_history_display[i][n_episode].append(states)

            if done or terminal:
                if len(dic_par_var_epi_len):
                    max_episode_len =\
                        get_variable_max_episode_len(dic_par_var_epi_len, n_episode)
                if arglist.display:
                    print_reward(n_episode, num_adversaries, train_step, agent_rewards,
                                 episode_rewards, 1, t_start)
                    t_start = time.time()
                    if n_episode >= arglist.num_episodes:
                        if arglist.video_record:
                            save_actions(action_history, arglist.video_file_name)
                            if is_trade(env):
                                save_every_state(states_history_display,
                                                 arglist.video_file_name)
                        if arglist.video_record and len(dic_messages) > 0:
                            save_messages(dic_messages, arglist.video_file_name)
                        if arglist.video_record:
                            recorder.env.close()
                        break
                if is_trade(env):
                    for i in range(env.n):
                        states_history[i].append(states_episode[i])
                        states_episode[i] = []

                obs_n = env.reset()
                episode_step = 0
                episode_rewards.append(0)
                for a in agent_rewards:
                    a.append(0)
                agent_info.append([[]])
                n_episode += 1

            # increment global step counter
            train_step += 1

            # for benchmarking learned policies
            if arglist.benchmark:
                for i, info in enumerate(info_n):
                    agent_info[-1][i].append(info_n['n'])
                if train_step > arglist.benchmark_iters and (done or terminal):
                    file_name = arglist.benchmark_dir + arglist.exp_name + '.pkl'
                    print('Finished benchmarking, now saving...')
                    with open(file_name, 'wb') as fp:
                        pickle.dump(agent_info[:-1], fp)
                    break
                continue

            # for displaying learned policies
            if arglist.display:
                # n_episode = len(episode_rewards)
                dic_messages[n_episode].append(get_messages(env.agents))
                time.sleep(arglist.display_sleep_second)
                if arglist.video_record:
                    recorder.capture_frame()
                else:
                    env.render()
                if False:
                    for i, agent in enumerate(trainers):
                        print(i, obs_n[i], rew_n[i])
                continue  # <- In the dispaly mode, no training (= we don't go down from here)

            # update all trainers, if not in display or benchmark mode
            loss = None
            for agent in trainers:
                agent.preupdate()
            for agent in trainers:
                loss = agent.update(trainers, train_step)

            # save model, display training output
            # n_episode = len(episode_rewards)
            if terminal and (n_episode % arglist.save_rate == 0):
                print(n_episode, max_episode_len)
                # print statement depends on whether or not there are adversaries
                print_reward(n_episode, num_adversaries, train_step, agent_rewards,
                             episode_rewards, arglist.save_rate, t_start)

                t_start = time.time()
                # Keep track of final episode reward
                final_ep_rewards.append(np.mean(episode_rewards[-arglist.save_rate:]))
                final_ep_ag_rewards.append([np.mean(rew[-arglist.save_rate:])
                                            for rew in agent_rewards])
                save_curves(n_episode, train_step,
                            final_ep_rewards[-1], final_ep_ag_rewards[-1], arglist)

                if is_trade(env):
                    save_states(n_episode, train_step, states_history, arglist)
                # thin out the saved models; 10 and 5 can be any int values.
                if ((n_episode < arglist.save_rate * 10) or
                    (n_episode % (arglist.save_rate * 5) == 0)):
                    save_model(saver, arglist, episode_rewards, n_episode)
                last_saved_episode = n_episode

            # saves final episode reward for plotting training curve later
            if n_episode >= arglist.num_episodes:
                if n_episode > last_saved_episode:
                    save_model(saver, arglist, episode_rewards, n_episode)
                    save_curves(n_episode, train_step,
                                final_ep_rewards[-1], final_ep_ag_rewards[-1], arglist)
                    if is_trade(env):
                        save_states(n_episode, train_step, states_history, arglist)
                print('...Finished!')
                print('Trained episodes: %d -> %d' % (n_episode0, n_episode))
                print('Total time: %.2f hr' % ((time.time() - t_start0) / 3600.))
                break


if __name__ == '__main__':
    arglist = parse_args()
    train(arglist)
