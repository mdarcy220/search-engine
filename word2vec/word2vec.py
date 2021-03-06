import cntk as C
import numpy as np
import pickle
import os

from SampledSoftmax import cross_entropy_with_sampled_softmax
from TextTrainingData import TextTrainingData
from WordMinibatchSource import WordMinibatchSource
from cntk.train import Trainer
from cntk.learners import sgd, learning_rate_schedule, UnitType
from cntk.train.training_session import CheckpointConfig, training_session, minibatch_size_schedule


hidden_dim = 300
alpha = 0.75
num_of_samples = 15
allow_duplicates = False
learning_rate = 0.0025
clipping_threshold_per_sample = 5.0
num_epochs = 10
max_window_size = 2
subsampling_rate = 4e-5


def create_inputs(vocab_dim):
	input_vector = C.ops.input_variable(vocab_dim, np.float32, is_sparse=True)
	label_vector = C.ops.input_variable(vocab_dim, np.float32, is_sparse=True)
	return input_vector, label_vector

def create_model(input_vector, label_vector, freq_list, vocab_dim, hidden_dim):

	hidden_vector = C.layers.Embedding(hidden_dim)(input_vector)
	#hidden_vector = C.times(input_vector, weights1) + bias1

	smoothed_weights = np.float32(np.power(freq_list, alpha))
	sampling_weights = C.reshape(C.Constant(smoothed_weights), shape = (1,vocab_dim))

	return cross_entropy_with_sampled_softmax(hidden_vector, label_vector, vocab_dim, hidden_dim, num_of_samples, sampling_weights)

def do_subsampling(text_training_data, subsampling=1e-5, prog_freq=1e8):
	total_freq = sum(text_training_data.id2freq)
	normalized_id2freq = np.array(text_training_data.id2freq, dtype=np.float64) / total_freq

	text = text_training_data.docs[0]
	indexes_to_remove = []

	# Use batching to let Numpy vectorize and improve performance
	# This is over 5x faster comparted to without batching
	batch_size = 5000

	for i in range(len(text)//batch_size):
		word_ids = text[i*batch_size:i*batch_size+batch_size]
		nWords = len(word_ids)
		removal_probs = 1 - np.sqrt(subsampling / normalized_id2freq[word_ids])
		indexes_to_remove.extend(np.where(np.random.random(size=nWords) < removal_probs)[0]+(i*batch_size))
		if (i*batch_size) % prog_freq < batch_size:
			print('Processed {} ({:0.3f}%) so far. {} words for removal ({:0.1f}%).'.format(i*batch_size, 100.0*i*batch_size/len(text), len(indexes_to_remove), 100.0*len(indexes_to_remove)/(i*batch_size+1)))

	print('Processing {} word removals ({:0.2f}%)...'.format(len(indexes_to_remove), 100.0*len(indexes_to_remove)/len(text)))
	text_training_data.docs[0] = TextTrainingData.remove_indexes(text_training_data.docs[0], indexes_to_remove)


def train():
	print('Unpickling data (this could take a short while)')
	training_data = pickle.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'largedata', 'tmp_textdata.pickle'), 'rb'))
	print('Preprocessing data (this could take a LONG while)...')
	do_subsampling(training_data, subsampling=subsampling_rate, prog_freq=1e7)
	print('Preprocessing is done. Final # of training words: {}'.format(len(training_data.docs[0])))
	mb_source = WordMinibatchSource(training_data, max_window_size)
	mb_num_samples = 128
	mb_size = minibatch_size_schedule(mb_num_samples)

	freq_list = training_data.id2freq
	token2id = training_data.token2id
	vocab_dim = len(freq_list)
	print(vocab_dim)
	input_vector, label_vector = create_inputs(vocab_dim)

	z, cross_entropy, error = create_model(input_vector, label_vector, freq_list, vocab_dim, hidden_dim) 

	lr_schedule = learning_rate_schedule(learning_rate, UnitType.sample)
	lr_schedule2 = learning_rate_schedule([(3e-3)*(0.8**i) for i in range(10)], UnitType.sample, epoch_size=len(training_data.docs[0])//2)
	mom_schedule = C.learners.momentum_schedule(0.005, UnitType.sample)
	gradient_clipping_with_truncation = True
	learner = C.learners.sgd(z.parameters, lr=lr_schedule2,
			    gradient_clipping_threshold_per_sample=clipping_threshold_per_sample,
			    gradient_clipping_with_truncation=gradient_clipping_with_truncation)

#	var_mom_schedule = C.learners.momentum_schedule(0.999, UnitType.sample)
#	learner2 = C.learners.adam(z.parameters,
#		lr=lr_schedule,
#		momentum=mom_schedule,
#		variance_momentum=var_mom_schedule,
#		epsilon=1.5e-8,
#		gradient_clipping_threshold_per_sample=clipping_threshold_per_sample,
#		gradient_clipping_with_truncation=gradient_clipping_with_truncation)

	progress_printer = C.logging.ProgressPrinter(freq=200, tag='Training')
	checkpoint_config = CheckpointConfig(frequency = 100000*mb_num_samples,
                                           filename = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'largedata', "word2vec_checkpoint"),
                                           restore = False)

	trainer = Trainer(z, (cross_entropy, error), [learner], progress_writers=[progress_printer])
	
	input_map = { input_vector: mb_source.fsi, label_vector: mb_source.lsi }	

	session = training_session(trainer, mb_source, mb_size, input_map, progress_frequency=len(training_data.docs[0]), max_samples = None, checkpoint_config=checkpoint_config, cv_config=None, test_config=None)
	
	C.logging.log_number_of_parameters(z) ; print()
	session.train()
train()
