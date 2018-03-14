import argparse
import sys
import math
from collections import namedtuple
from itertools import count

import gym
import numpy as np
import scipy.optimize
from gym import wrappers

import torch
import torch.autograd as autograd
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import torchvision.transforms as T
from torch.autograd import Variable

from models import Policy, Value, ActorCritic
from replay_memory import Memory
from running_state import ZFilter

import plotly
import plotly.graph_objs as go
from plotly.graph_objs import Layout,Scatter

PI = torch.DoubleTensor([3.1415926])

def normal_log_density(x, mean, log_std, std):
    var = std.pow(2)
    log_density = -(x - mean).pow(2) / (2 * var) - 0.5 * torch.log(2 * Variable(PI)) - log_std
    return log_density.sum(1)

def select_action(state, policy_net):
    torch.set_default_tensor_type('torch.DoubleTensor')
    PI = torch.DoubleTensor([3.1415926])
    state = torch.from_numpy(state).unsqueeze(0)
    action_mean, _, action_std = policy_net(Variable(state))
    action = torch.normal(action_mean, action_std)
    return action

def normal_log_density(x, mean, log_std, std):
    torch.set_default_tensor_type('torch.DoubleTensor')
    PI = torch.DoubleTensor([3.1415926])
    var = std.pow(2)
    log_density = -(x - mean).pow(2) / (2 * var) - 0.5 * torch.log(2 * Variable(PI)) - log_std
    return log_density.sum(1)

def select_action_actor_critic(state):
    torch.set_default_tensor_type('torch.DoubleTensor')
    PI = torch.DoubleTensor([3.1415926])
    state = torch.from_numpy(state).unsqueeze(0)
    action_mean, _, action_std, v = ac_net(Variable(state))
    action = torch.normal(action_mean, action_std)
    return action

def update_params(batch, policy_net, value_net, gamma, opt_policy, opt_value):
    torch.set_default_tensor_type('torch.DoubleTensor')
    PI = torch.DoubleTensor([3.1415926])
    rewards = torch.Tensor(batch.reward)
    masks = torch.Tensor(batch.mask)
    actions = torch.Tensor(np.concatenate(batch.action, 0))
    states = torch.Tensor(batch.state)
    values = value_net(Variable(states))

    returns = torch.Tensor(actions.size(0),1)
    deltas = torch.Tensor(actions.size(0),1)
    advantages = torch.Tensor(actions.size(0),1)

    prev_return = 0
    prev_value = 0
    prev_advantage = 0
    for i in reversed(range(rewards.size(0))):
        returns[i] = rewards[i] + args.gamma * prev_return * masks[i]
        deltas[i] = rewards[i] + args.gamma * prev_value * masks[i] - values.data[i]
        advantages[i] = deltas[i] + args.gamma * args.tau * prev_advantage * masks[i]
        prev_return = returns[i, 0]
        prev_value = values.data[i, 0]
        prev_advantage = advantages[i, 0]

    targets = Variable(returns)

    opt_value.zero_grad()
    value_loss = (values - targets).pow(2.).mean()
    value_loss.backward()
    opt_value.step()

    # kloldnew = policy_net.kl_old_new() # oldpi.pd.kl(pi.pd)
    # ent = policy_net.entropy() #pi.pd.entropy()
    # meankl = torch.reduce_mean(kloldnew)
    # meanent = torch.reduce_mean(ent)
    # pol_entpen = (-args.entropy_coeff) * meanent

    action_var = Variable(actions)

    action_means, action_log_stds, action_stds = policy_net(Variable(states))
    log_prob_cur = normal_log_density(action_var, action_means, action_log_stds, action_stds)

    action_means_old, action_log_stds_old, action_stds_old = policy_net(Variable(states), old=True)
    log_prob_old = normal_log_density(action_var, action_means_old, action_log_stds_old, action_stds_old)

    # backup params after computing probs but before updating new params
    policy_net.backup()

    advantages = (advantages - advantages.mean()) / advantages.std()
    advantages_var = Variable(advantages)

    opt_policy.zero_grad()
    ratio = torch.exp(log_prob_cur - log_prob_old) # pnew / pold
    surr1 = ratio * advantages_var[:,0]
    surr2 = torch.clamp(ratio, 1.0 - args.clip_epsilon, 1.0 + args.clip_epsilon) * advantages_var[:,0]
    policy_surr = -torch.min(surr1, surr2).mean()
    policy_surr.backward()
    torch.nn.utils.clip_grad_norm(policy_net.parameters(), 40)
    opt_policy.step()


def main(gamma=0.995, env_name='Walker2d-v2', tau=0.97, seed=543, number_of_batches=500,\
        batch_size=5000, maximum_steps=10000, render=False, log_interval=1, entropy_coeff=0.0,\
        clip_epsilon=0.2, use_joint_pol_val=False):

    torch.set_default_tensor_type('torch.DoubleTensor')
    PI = torch.DoubleTensor([3.1415926])

    env = gym.make(env_name)

    num_inputs = env.observation_space.shape[0]
    num_actions = env.action_space.shape[0]

    env.seed(seed)
    torch.manual_seed(seed)

    policy_net = Policy(num_inputs, num_actions)
    value_net = Value(num_inputs)
    opt_policy = optim.Adam(policy_net.parameters(), lr=0.001)
    opt_value = optim.Adam(value_net.parameters(), lr=0.001)

    running_state = ZFilter((num_inputs,), clip=5)
    running_reward = ZFilter((1,), demean=False, clip=10)
    episode_lengths = []
    plot_rew = []
    for i_episode in range(number_of_batches):
        memory = Memory()

        num_steps = 0
        reward_batch = 0
        num_episodes = 0
        while num_steps < batch_size:
            state = env.reset()
            state = running_state(state)

            reward_sum = 0
            for t in range(maximum_steps): # Don't infinite loop while learning
                action = select_action(state, policy_net)
                action = action.data[0].numpy()
                next_state, reward, done, _ = env.step(action)
                reward_sum += reward

                next_state = running_state(next_state)

                mask = 1
                if done:
                    mask = 0

                memory.push(state, np.array([action]), mask, next_state, reward)

                if render:
                    env.render()
                if done:
                    break

                state = next_state
            num_steps += (t-1)
            num_episodes += 1
            reward_batch += reward_sum

        reward_batch /= num_episodes
        batch = memory.sample()
        plot_rew.append(reward_batch)
        update_params(batch, policy_net, value_net, gamma, opt_policy, opt_value)

        if i_episode % args.log_interval == 0:
            print('Episode {}\tLast reward: {}\tAverage reward {:.2f}'.format(
                i_episode, reward_sum, reward_batch))
    
    plot_epi = []
    for i in range (number_of_batches):
        plot_epi.append(i)
    trace = go.Scatter( x = plot_epi, y = plot_rew) 
    layout = go.Layout(title='PPO',xaxis=dict(title='Episodes', titlefont=dict(family='Courier New, monospace',size=18,color='#7f7f7f')),
    yaxis=dict(title='Average Reward', titlefont=dict(family='Courier New, monospace',size=18,color='#7f7f7f')))

    plotly.offline.plot({"data": [trace], "layout": layout},filename='PPO.html',image='jpeg')

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='PyTorch actor-critic example')
    parser.add_argument('--gamma', type=float, default=0.995, metavar='G',
                        help='discount factor (default: 0.995)')
    parser.add_argument('--env-name', default="Walker2d-v2", metavar='G',
                        help='name of the environment to run')
    parser.add_argument('--tau', type=float, default=0.97, metavar='G',
                        help='gae (default: 0.97)')
    # parser.add_argument('--l2_reg', type=float, default=1e-3, metavar='G',
    #                     help='l2 regularization regression (default: 1e-3)')
    # parser.add_argument('--max_kl', type=float, default=1e-2, metavar='G',
    #                     help='max kl value (default: 1e-2)')
    # parser.add_argument('--damping', type=float, default=1e-1, metavar='G',
    #                     help='damping (default: 1e-1)')
    parser.add_argument('--seed', type=int, default=543, metavar='N',
                        help='random seed (default: 1)')
    parser.add_argument('--number-of-batches', type=int, default=50, metavar='N',
                        help='number of batches (default: 500)')
    parser.add_argument('--batch-size', type=int, default=20, metavar='N',
                        help='batch size (default: 5000)')
    parser.add_argument('--maximum_steps', type=int, default=10, metavar='N',
                        help='maximum number of steps (default: 10000)')
    parser.add_argument('--render', action='store_true',
                        help='render the environment')
    parser.add_argument('--log-interval', type=int, default=1, metavar='N',
                        help='interval between training status logs (default: 10)')
    parser.add_argument('--entropy-coeff', type=float, default=0.0, metavar='N',
                        help='coefficient for entropy cost')
    parser.add_argument('--clip-epsilon', type=float, default=0.2, metavar='N',
                        help='Clipping for PPO grad')
    parser.add_argument('--use-joint-pol-val', action='store_true',
                        help='whether to use combined policy and value nets')
    args = parser.parse_args()
    main(args.gamma, args.env_name, args.tau, args.seed, args.number_of_batches,\
            args.batch_size, args.maximum_steps, args.render, args.log_interval,\
            args.entropy_coeff, args.clip_epsilon)
