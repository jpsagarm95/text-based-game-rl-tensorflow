import time
from tqdm import tqdm
import random
import numpy as np
import tensorflow as tf
from collections import deque

from .base import Model



import os
from history import History
from replay_memory import ReplayMemory
import cPickle as pickle

class DQN:
    def __init__(self, config):

        #init replay memory
        self.config = config
        
        self.memory = self.load_replay_memory(config)
        self.history = History(config)
        #init parameters
        self.timeStep = 0
        self.epsilon = config.INITIAL_EPSILON
        self.actions = config.NUM_ACTIONS

        self.stateInput = tf.placeholder(tf.int32, [None, self.config.seq_length])
        self.stateInputT = tf.placeholder(tf.int32, [None, self.config.seq_length])


        embed = tf.get_variable("embed", [self.config.vocab_size, self.config.embed_dim])
        embedT = tf.get_variable("embed", [self.config.vocab_size, self.config.embed_dim])

        word_embeds = tf.nn.embedding_lookup(embed, self.stateInput) # @codewalk: What is this line doing ?
        word_embedsT = tf.nn.embedding_lookup(embedT, self.stateInputT) # @codewalk: What is this line doing ?

        self.initializer = tf.truncated_normal(shape, stddev = 0.02)

        self.cell = tf.nn.rnn_cell.LSTMCell(self.config.rnn_size, initializer = self.initializer)
        self.cellT = tf.nn.rnn_cell.LSTMCell(self.config.rnn_size, initializer = self.initializer)

        initial_state = self.cell.zero_state(None, tf.float32)
        initial_stateT = self.cellT.zero_state(None, tf.float32)

        early_stop = tf.constant(self.config.seq_length, dtype = tf.int32)

        outputs, _ = tf.nn.rnn(self.cell, [tf.reshape(embed_t, [-1, self.config.embed_dim]) for embed_t in tf.split(1, self.config.seq_length, word_embeds)], dtype=tf.float32, initial_state = initial_state, sequence_length = early_stop, scope = "LSTM")
        outputsT, _ = tf.nn.rnn(self.cellT, [tf.reshape(embed_tT, [-1, self.config.embed_dim]) for embed_tT in tf.split(1, self.config.seq_length, word_embedsT)], dtype=tf.float32, initial_state = initial_stateT, sequence_length = early_stop, scope = "LSTMT")

        output_embed = tf.transpose(tf.pack(outputs), [1, 0, 2])
        output_embedT = tf.transpose(tf.pack(outputsT), [1, 0, 2])

        mean_pool = tf.nn.relu(tf.reduce_mean(output_embed, 1))
        mean_poolT = tf.nn.relu(tf.reduce_mean(output_embedT, 1))

        linear_output = tf.nn.relu(tf.nn.rnn_cell._linear(mean_pool, int(output_embed.get_shape()[2]), 0.0, scope="linear"))
        linear_outputT = tf.nn.relu(tf.nn.rnn_cell._linear(mean_poolT, int(output_embedT.get_shape()[2]), 0.0, scope="linearT"))


        self.action_value = tf.nn.rnn_cell._linear(linear_output, self.config.num_action, 0.0, scope="action")
        self.action_valueT = tf.nn.rnn_cell._linear(linear_outputT, self.config.num_action, 0.0, scope="actionT")

        self.object_value = tf.nn.rnn_cell._linear(linear_output, self.config.num_object, 0.0, scope="object")
        self.object_valueT = tf.nn.rnn_cell._linear(linear_outputT, self.config.num_object, 0.0, scope="objectT")

        self.target_action_value = tf.placeholder(tf.float32, [None])
        self.target_object_value = tf.placeholder(tf.float32, [None])

        self.action_indicator = tf.placeholder(tf.float32, [None, self.config.num_action])
        self.object_indicator = tf.placeholder(tf.float32, [None, self.config.num_object])

        self.pred_action_value = tf.reduce_sum(tf.mul(self.action_indicator, self.action_value), 1)
        self.pred_object_value = tf.reduce_sum(tf.mul(self.object_indicator, self.object_value), 1)

        self.target_qpred = (self.target_action_value + self.target_object_value)/2
        self.qpred = (self.pred_action_value + self.pred_object_value)/2

        self.delta = self.target_qpred - self.qpred

        if self.config.clipDelta:
            self.delta = tf.clip_by_value(self.delta, self.config.minDelta, self.config.maxDelta, name='clipped_delta')

        self.loss = tf.reduce_mean(tf.square(self.delta), name='loss')

        self.W = ["LSTM", "linear", "action", "object"]
        self.target_W = ["LSTMT", "linearT", "actionT", "objectT"]


        # Clipping gradients

        self.optim_ = tf.train.RMSPropOptimizer(learning_rate = self.config.LEARNING_RATE, decay = 1, momentum = self.config.GRADIENT_MOMENTUM)
        tvars = tf.trainable_variables()
        def ClipIfNotNone(grad,var):
            if grad is None:
                return grad
            return tf.clip_by_norm(grad,20)
        grads = [ClipIfNotNone(i,var) for i,var in zip(tf.gradients(self.loss, tvars),tvars)]
        self.optim = self.optim_.apply_gradients(zip(grads, tvars))


        if not(self.config.LOAD_WEIGHTS and self.load_weights()):
            self.session.run(tf.initialize_all_variables())
            # self.copyTargetQNetworkOperation()


    def copyTargetQNetworkOperation(self):
        for i in range(len(self.W)):
            vars = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope = self.W[i])
            varsT = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, scope = self.target_W[i])
            copy_op = zip(vars, varsT)
            self.session.run(map(lambda (x,y): x.assign(y.eval(session = self.session)),copy_op))


    def train(self):

        s_t, action, obj, reward, s_t_plus_1, terminal = self.memory.sample()
        state_batch = s_t
        action_batch = action
        obj_batch = obj
        reward_batch = reward
        nextState_batch = s_t_plus_1

        # Step 2: calculate y
        target_action_batch = []
        target_object_batch = []
        QValue_action_batch = self.action_valueT.eval(feed_dict={self.stateInputT:nextState_batch},session = self.session)
        QValue_object_batch = self.object_valueT.eval(feed_dict={self.stateInputT:nextState_batch},session = self.session)


        for i in range(0,self.config.BATCH_SIZE):
            if terminal[i]:
                target_action_batch.append(reward_batch[i])
                target_object_batch.append(reward_batch[i])
            else:
                target_action_batch.append(reward_batch[i] + self.config.GAMMA* np.max(QValue_action_batch[i]))
                target_object_batch.append(reward_batch[i] + self.config.GAMMA* np.max(QValue_object_batch[i]))

        self.optim.run(feed_dict={
                self.target_action_value : target_action_batch,
                self.target_object_value : target_object_batch,
                self.action_indicator : action_batch,
                self.object_indicator : obj_batch,
                self.stateInput : state_batch
                },session = self.session)

        # save network every 10000 iteration
        if self.timeStep % 10000 == 0:
            if not os.path.exists(os.getcwd()+'/Savednetworks'):
                os.makedirs(os.getcwd()+'/Savednetworks')
            self.saver.save(self.session, os.getcwd()+'/Savednetworks/'+'network' + '-dqn', global_step = self.timeStep)

        if self.timeStep % self.config.UPDATE_FREQUENCY == 0:
            self.copyTargetQNetworkOperation()

    def setPerception(self,state,action,reward,nextstate,terminal,evaluate = False): #nextObservation,action,reward,terminal):
        self.history.add(nextstate)
        if not evaluate:
            self.memory.add(state,reward,action,nextstate,terminal)
        if self.timeStep > self.config.REPLAY_START_SIZE and self.memory.count > self.config.REPLAY_START_SIZE:
            # Train the network
            if not evaluate and self.timeStep % self.config.trainfreq ==0:
                self.train()
        if not evaluate:
            self.timeStep += 1


    def getAction(self, evaluate = False):
        action_index = 0
        object_index = 0
        curr_epsilon = self.epsilon
        if evaluate:
            curr_epsilon = 0.05
            
        if random.random() <= curr_epsilon:
            action_index = random.randrange(self.actions)
            object_index = random.randrange(self.objects)
        else:
            QValue_action = self.action_value.eval(feed_dict={self.stateInput:[self.history.get()]},session = self.session)[0]
            Qvalue_object = self.object_value.eval(feed_dict={self.stateInput:[self.history.get()]},session = self.session)[0]
            action_index = np.argmax(QValue_action)
            object_index = np.argmax(QValue_object)


        if not evaluate:
            if self.epsilon > self.config.FINAL_EPSILON and self.timeStep > self.config.REPLAY_START_SIZE:
                self.epsilon -= (self.config.INITIAL_EPSILON - self.config.FINAL_EPSILON) / self.config.EXPLORE

        return action_index, object_index
    
    def load_weights(self):
        print 'inload weights'
        if not os.path.exists(os.getcwd()+'/Savednetworks'):
            return False    
        
        list_dir = sorted(os.listdir(os.getcwd()+'/Savednetworks'))
        if not any(item.startswith('network-dqn') for item in list_dir):
            return False
        
        print 'weights loaded'
        self.saver.restore(self.session, os.getcwd()+'/Savednetworks/'+list_dir[-2])        
        return True

    
    def load_replay_memory(self,config):
        if os.path.exists(config.model_dir+'/replay_file.save'):
            fp = open(config.model_dir+'/replay_file.save','rb')
            memory = pickle.load(fp)
            fp.close()
        else:
            memory = ReplayMemory(config)
        return memory
          

