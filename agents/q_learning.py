import os 
import time 
import random
import numpy as np
from tqdm import tqdm
import tensorflow as tf
import pprint
import argparse
import inspect
from util.replay_memory import ReplayMemory

pp = pprint.PrettyPrinter().pprint
def class_vars(obj):
    return {k:v for k,v in inspect.getmembers(obj) if not k.startswith('__') and not callable(k)}


class Agent():
    def __init__(self, config, sess):
        self.config = config
        try:
            self._attrs = config.__dict__['__flags']
        except:
            self._attrs = class_vars(config)
        for attr in self._attrs:
            name = attr if not attr.startswith('_') else attr[1:]
            setattr(self, name, getattr(config, attr))
        model_module = __import__(os.path.join("envs."+self.env_name))
        self.env = getattr(getattr(model_module,self.env_name),self.env_name)(config)
        _, _, self.config.actions = self.env.new_game()
        self.config.action_num = len(self.config.actions)
        self.config.state_num = self.env.state_num
        model_module = __import__(os.path.join("models."+self.model_name))
        self.model = getattr(getattr(model_module,self.model_name),self.model_name)(config)
        print(self.model)
        self.sess = sess
        self.model.sess = self.sess

        self.writer = tf.train.SummaryWriter('./logs/%s' % self.model.model_dir, self.sess.graph)
        random.seed(config.random_seed)
        with tf.variable_scope("step"):
            self.step_op = tf.Variable(0, trainable=False, name="step")
        with tf.variable_scope("summary"):
            scalar_summary_tags = ['average.reward','average.loss','episode.max reward', 'episode.min reward', 'episode.avg reward', 'episode.num of game']
            self.summary_placeholders = {}
            self.summary_ops = {}
            for tag in scalar_summary_tags:
                self.summary_placeholders[tag] = tf.placeholder('float32', None, name = tag.replace(' ', '_'))
                self.summary_ops[tag] = tf.scalar_summary("%s-%s/%s" % (self.env_name, self.env_type, tag), self.summary_placeholders[tag])
            histogram_summary_tags = ['episode.rewards', 'episode.actions']
            for tag in histogram_summary_tags:
                self.summary_placeholders[tag] = tf.placeholder('float32', None, name=tag.replace(' ','_'))
                self.summary_ops[tag] = tf.histogram_summary(tag, self.summary_placeholders[tag])
        pp(class_vars(self.config))
        tf.initialize_all_variables().run()
        self.memory = ReplayMemory(config)
            

    def observe(self, state, reward, action, terminal):
        reward = max(self.min_reward, min(self.max_reward, reward))
        if self.step > self.learn_start:
            if self.step % self.train_frequency == 0:
                self.q_learning_mini_batch()
        

    #with training
    def run(self):
        if self.config.is_train:
            num_game, ep_reward = 0,0.
            total_reward, self.total_loss, total_q = 0.,0.,0.
            max_avg_ep_reward = 0
            ep_rewards = []
            start_step = 0
            start_time = time.time()
            state, terminal, _ = self.env.new_game()
            actions = []
            predict_times = []
            step_times = []
            observe_times = []
            
            for self.step in tqdm(range(start_step, self.max_step), ncols=70, initial=start_step):
                if self.step == self.learn_start:
                    self.update_count = 0
                    num_game= 0
                    total_reward, self.total_loss, total_q = 0.,0.,0.
                    max_avg_ep_reward = 0
                    ep_rewards,actions = [],[]

                time1 = time.time()
                action = self.model.predict(self.env.history.get())
                time2 = time.time()
                reward, state, terminal = self.env.step(action)
                self.memory.add(state, reward, action, terminal) 
                time3 = time.time()
                self.observe(state, reward, action, terminal)
                time4 = time.time()
                predict_times.append(time2-time1)
                step_times.append(time3-time2)
                observe_times.append(time4-time3)
                ep_reward += reward
                actions.append(action)
                total_reward += reward

                if terminal:
                    state, terminal = self.env.reset()
                    num_game += 1
                    ep_rewards.append(ep_reward)
                    ep_reward = 0.
                
                if self.step >= self.learn_start:
                    if (self.step-self.learn_start+1) % self.test_step == 0:
                        avg_reward = total_reward/self.test_step
                        avg_loss = self.total_loss/self.update_count

                        try:
                            max_ep_reward = np.max(ep_rewards)
                            min_ep_reward = np.min(ep_rewards)
                            avg_ep_reward = np.mean(ep_rewards)
                        except:
                            max_ep_reward,min_ep_reward,avg_ep_reward = 0.,0.,0.
                        print("predict time : %f, step time: %f observe time %f" % (np.mean(predict_times), np.mean(step_times), np.mean(observe_times)))
                        print ('\navg_r: %.4f, avg_l: %.6f, avg_ep_r: %.4f, max_ep_r: %.4f, min_ep_r: %.4f, # game: %d' \
                          % (avg_reward, avg_loss, avg_ep_reward, max_ep_reward, min_ep_reward, num_game))

                        if max_avg_ep_reward * 0.9 <= avg_ep_reward:
                            print("save model : ",self.step)
                            tf.assign(self.step_op, self.step+1).eval()
                            self.model.save_model(self.step + 1)
                            max_avg_ep_reward = max(max_avg_ep_reward, avg_ep_reward)
                        if self.step > 18000:
                            self.inject_summary({
                                'average.reward': avg_reward,
                                'average.loss': avg_loss,
                                'episode.max reward': max_ep_reward,
                                'episode.min reward': min_ep_reward,
                                'episode.avg reward': avg_ep_reward,
                                'episode.num of game': num_game,
                                'episode.rewards': ep_rewards,
                                'episode.actions': actions,
                                },self.step)

                        num_game = 0
                        total_reward = 0.
                        self.total_loss = 0.
                        self.total_q = 0.
                        self.update_count = 0
                        predict_times = []
                        step_times = []
                        observe_times = []
                        ep_rewards = []
                        actions = []
                        print(self.model.w.eval())

            print("learning: %d" % self.model.learn_count.eval())
        else:
            if self.test_ep == None:
                self.test_ep = self.ep_end
            
#if not self.display:
#               env_dir = "/tmp/%s-%s" % (self.env_name, get_time())
#               self.env.env.monitor.start(env_dir)
            best_reward, best_idx = 0, 0
            all_reward = 0.
            for idx in range(self.n_episode):
                state, terminal, _ = self.env.new_game()
                current_reward = 0

                for t in tqdm(range(self.n_step), ncols=70):
                    action = self.model.predict(self.env.history.get(),self.test_ep)
                    reward, state, terminal = self.env.step(action)
                    current_reward += reward
                    if terminal:
                        break
                all_reward += current_reward

                if current_reward > best_reward:
                    best_reward = current_reward
                    best_idx = idx
                print("="*30)
                print(" [%d] This reward : %d" % (idx, current_reward))
                print("="*30)

#            if not self.display:
#               self.env.env.monitor.close()
            print("="*30)
            print("="*30)
            print(" [%d] Best reward : %d" % (best_idx, best_reward))
            print(" Average rewards of a game: %f/%d = %f]" % (all_reward, self.n_episode, all_reward/self.n_episode))

       
    
    def inject_summary(self, tag_dict, step):
        summary_str_lists = self.sess.run([self.summary_ops[tag] for tag in tag_dict.keys()], {
          self.summary_placeholders[tag]: value for tag, value in tag_dict.items()
        })
        for summary_str in summary_str_lists:
          self.writer.add_summary(summary_str, self.step)
        


    def q_learning_mini_batch(self):
        if self.memory.count <= self.history_length:
            return
        else:
            states, actions, rewards, next_states, terminals = self.memory.sample_more()
        loss, summary_str = self.model.learn(states, actions, rewards, next_states, terminals)
        self.writer.add_summary(summary_str, self.step)
        self.total_loss += loss
        self.update_count += 1

                                
                                






