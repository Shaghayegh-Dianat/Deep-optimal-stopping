# -*- coding: utf-8 -*-
"""DOS_main.ipynb

Automatically generated by Colab.

Original file is located at
    https://colab.research.google.com/drive/1s75sz4A5j_392sRMoujfn8xGBHFZzMOS
"""

import numpy as np
import torch.nn as nn
import torch
import matplotlib.pyplot as plt
import time
device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
print(device)

#Parameters
T = 3.0    # final time
N = 9  # number of stopping times
dt = T/N  #time intervals
batch_size = 8192

#Simulate Geometric brownian motion paths
def GBM(d, mu, sigma, S0, T, dt, number_of_paths, seed=None):
    """
    Efficiently simulates number_of_paths of d-dimensional geometric brownian motion (GBM) sample paths.

    Arguments:
    d :The dimension(number of stocks) of the GBM to be simulated.
    mu : Drift values in an array of shape (d,).
    sigma : Volatilities in an array of shape (d,).
    S0 : Initial values of the GBM in an array of shape (d,).
    T : The maturity time of the option.
    dt : Time increments.
    number_of_paths : Number of sample paths to be simulated.
    seed :The seed for the random number generator to ensure reproducibility.

    Returns:
    An array of GBM simulations of shape (number_of_paths, d, n) where n = T/dt.
    """
    if seed is not None:
        np.random.seed(seed)  # Set the seed if provided

    n = int(T / dt)  # number of time steps
    dt_sqrt = np.sqrt(dt)

    # Precompute drift and diffusion terms
    drift_term = (mu - 0.5 * sigma**2) * dt
    diffusion_term = sigma * dt_sqrt

    # Initialize the simulations array
    S = np.empty((number_of_paths, d, n + 1), dtype=np.float32)
    S[:, :, 0] = S0

    # Simulate paths
    for t in range(1, n + 1):
        Z = np.random.randn(number_of_paths, d).astype(np.float32)
        S[:, :, t] = S[:, :, t-1] * np.exp(drift_term + diffusion_term * Z) # exact solution of GBM

    return S

#computes g values of a brownian motion of the form of output of above function
def g(x,r,k,dt):
  """
  Computes the discounted payoff of a European call option at time 0.
  Parameters:
    x : The simulated paths of the GBM.
    r : The risk-free interest rate.
    k : The strike price.
    dt : The time increment.
    Returns:
        The discounted payoff of a call option at time 0.
  """
  y = np.maximum(np.amax(x - k, axis = 1), 0) #max(S1,...,Sd) - k
  z = np.ones((x.shape[0], x.shape[2])) # x.shape[0] is number of paths, x.shape[2] is number of time steps
  z[:, 0] = np.zeros((x.shape[0])) #initialize z0 = 0
  z = -r*dt*np.cumsum(z, axis =1)
  z = np.exp(z) # e^(-r*t), discount factor
  return y * z # g = (max(S1,...,Sd) - k)^(+) * e^(-r*t), discounted back to time 0

#Creates neural network
def create_model(d):
    """
    Creates a neural network with 2 hidden layers of 40+d units
    """
    model = nn.Sequential(
    nn.Linear(d, d+40), # input layer
    nn.BatchNorm1d(40+d), # batch normalization
    nn.ReLU(), # activation function
    nn.Linear(d+40, d+40),
    nn.BatchNorm1d(d+40),
    nn.ReLU(),
    nn.Linear(d+40, 1),
    nn.Sigmoid()
    )
    return model

#initiates dictionaries for f,F,l at maturity time N
#that will contain functions F (soft stopping decision),f (stopping decision) and l (stopping time)
def fN(x):
    return 1 # at maturity we have to stop
def FN(x):
    return 1.0 # at maturity we have to stop
def lN(x):    #can take input a vector of values
    """
    Argument:
    x: a tensor of shape (k,d,1) which contains Nth values of brownian paths for k samples
    Outputs:
    Stopping times at maturity as a tensor of shape (k, ).
    """
    ans = N  * np.ones(shape = (x.shape[0], ))
    ans = ans.astype(int)
    return ans


l = {N: lN} # dictionary containing stopping times, initialized with lN
f = {N: fN} #dictionary containing hard stopping decisions, initialized with fN
F = {N: FN} #dictionary containing soft stopping decisions, initialized with FN

#initiates dictionaries for f,F,l at time i<N
def train(X, r, k, dt, model, i, optimizer, number_of_training_steps, batch_size):
  """
  Trains the model for the ith stopping time where i is between 0 and N-1
  Arguments:
  X: tensor of shape (3000+d, 8192, d, 10) containing paths r
  r: risk free rate
  k: strike price
  dt: time interval
  model: neural network model
  i: stopping time index
  """
  for j in range(number_of_training_steps):
    batch = X[j] #batch of paths
    batch_now = batch[:, :, i] # the ith stopping time values
    discounted_payoffs = g(batch,r,k,dt) # discounted payoff values at ith stopping time
    immediate_payoffs = discounted_payoffs[:, i].reshape(1, batch_size) # reshaping to make it compatible with the model
    batch = torch.from_numpy(batch).float().to(device) # storing the batch in the device
    continuation_values = discounted_payoffs[range(batch_size), l[i+1](batch)].reshape(1, batch_size)
    batch_now = torch.from_numpy(batch_now).float().to(device)
    immediate_payoffs = torch.from_numpy(immediate_payoffs).float().to(device)
    continuation_values = torch.from_numpy(continuation_values).float().to(device)

    #compute loss
    stopping_probability = model(batch_now) # model output
    ans1 = torch.mm(immediate_payoffs, stopping_probability) # term 1: immediate payoff * stopping probability
    ans2 = torch.mm( continuation_values, 1.0 - stopping_probability)  # term 2: continuation value * (1 - stopping probability)
    loss = - 1 / batch_size * (ans1 + ans2) # loss = -E[g(i,X_i)F + C_{i+1}(1-F)]

    #apply updates
    optimizer.zero_grad() # zero the gradients
    loss.backward() # backpropagation
    optimizer.step() # update the weights

  print(f"the model for {i}th stopping time has been trained")


def fi(x, i, F):
    """
    the function that returns the stopping decision for ith stopping time
    Arguments:
    x: a tensor of shape (k, d) which contains ith values of brownian paths for k samples
    i: ith stopping time
    F: dictionary of models
    Outputs:
    hard Stopping decisions as a tensor of shape (k, ). (in this case it will just output 1 if x >= 1/2 else 0)
    """
    func = F[i].eval()
    return torch.ceil(func(x) - 1/2)

def li(x, i, f, l):
    """
    the function that returns the stopping time at ith stopping time
    Arguments:
    x: a tensor of shape (k, d) which contains ith values of brownian paths for k samples
    i: ith stopping time
    f: dictionary of stopping decision functions
    l: dictionary of stopping time functions
    Outputs:
    li Stopping times as a tensor of shape (k, ).
    """
    a = f[i](x[:,:,i]).cpu().detach().numpy().reshape(list(x[:,:,i].size())[0], )
    return ((i)*a + np.multiply(l[i+1](x), (1-a))).astype("int32")
    # Function to calculate MAEP
def mean_absolute_error_percentage(y_true, y_pred):
    return np.mean(np.abs((y_true - y_pred) / y_true)) * 100

r = 0.05  #interest rate
dividend = 0.1  #divident rate
seed_train = 1 # seed for training data
seed_test = 2 # seed for reproducibility
learning_rate = 0.001 #learning rate
k = 100.0  #strike price
test_paths = 100000 # number of paths for testing

"""d=2,S0=100"""

# Parameters
d = 2   #dimension of GBM
mu = (r - dividend) * np.ones(shape = (d, )) # drift
sigma = 0.2 * np.ones(shape = (d, )) #volatility
S0 = 100.0 * np.ones(shape = (d, )) #initial price
# we use lessp paths due to limited computational resources
base_steps = 1500 #number of training steps regardless of the dimension
total_paths = batch_size * (base_steps + d) #total number of paths, base_steps+d is the training steps, where each step we need 8192 paths
number_of_training_steps = int(total_paths / batch_size) #number of training steps

#Simulating GBM paths
X = GBM(d, mu, sigma, S0, T, dt, total_paths, seed=seed_train) #simulating GBM paths
X = X.reshape(base_steps+d, batch_size, d, 10) #reshaping to (1500+d, 8192, d, 10)

# training the model for each stopping time
start_time = time.time()  # Start the timer
for i in range(N-1, 0, -1):
    model = create_model(d).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr = learning_rate)
    train(X, r, k, dt, model, i, optimizer, number_of_training_steps, batch_size)
    # store the model and the stopping decision function and stopping time function
    F[i] = model
    f[i] = lambda x, i=i: fi(x, i, F) #  store the stopping decision function
    l[i] = lambda x, i=i: li(x, i, f, l) # store the stopping time function
end_time = time.time()  # End the timer

# test the model on new set of paths
X = GBM(d, mu, sigma, S0, T, dt, test_paths, seed = seed_test)
g_val = g(X, r, k, dt) # g values at stopping times
X = torch.from_numpy(X).float().to(device) # convert X to a tensor
Z = g_val[range(test_paths), l[1](X)] # g values at stopping times, l[1]
price = 1 / test_paths * np.sum(Z) # monte carlo estimate of the price
print(f"Estimated price of the {d}D Bermudan Max Call Option: {price:.3f}")
print(f"Time for training the model with {d} dimensions: {end_time - start_time:.2f} seconds")

y_true = 13.902 #imported from the reference
y_pred =price
# Compute metrics
maep = mean_absolute_error_percentage(y_true, y_pred)

# Display results
print(f"Mean Absolute Error Percentage (MAEP): {maep:.2f}%")

""" d=3 , S0=90

"""

# Parameters

d = 3   #dimension of GBM
mu = (r - dividend) * np.ones(shape = (d, )) # drift
sigma = 0.2 * np.ones(shape = (d, )) #volatility
S0 = 90.0 * np.ones(shape = (d, )) #initial price
# we use lessp paths due to limited computational resources
base_steps = 1500 #number of training steps regardless of the dimension
total_paths = batch_size * (base_steps + d) #total number of paths, base_steps+d is the training steps, where each step we need 8192 paths
number_of_training_steps = int(total_paths / batch_size) #number of training steps

#Simulating GBM paths
X = GBM(d, mu, sigma, S0, T, dt, total_paths, seed=seed_train) #simulating GBM paths
X = X.reshape(base_steps+d, batch_size, d, 10) #reshaping to (1500+d, 8192, d, 10)

# training the model for each stopping time
start_time = time.time()
for i in range(N-1, 0, -1):
    model = create_model(d).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr = learning_rate)
    train(X, r, k, dt, model, i, optimizer, number_of_training_steps, batch_size)
    # store the model and the stopping decision function and stopping time function
    F[i] = model
    f[i] = lambda x, i=i: fi(x, i, F) #  store the stopping decision function
    l[i] = lambda x, i=i: li(x, i, f, l) # store the stopping time function
end_time = time.time()

# test the model on new set of paths
X = GBM(d, mu, sigma, S0, T, dt, test_paths, seed = seed_test)
g_val = g(X, r, k, dt) # g values at stopping times
X = torch.from_numpy(X).float().to(device) # convert X to a tensor
Z = g_val[range(test_paths), l[1](X)] # g values at stopping times, l[1]
price = 1 / test_paths * np.sum(Z) # monte carlo estimate of the price
print(f"Estimated price of the {d}D Bermudan Max Call Option: {price:.3f}")
print(f"Time for training the model with {d} dimensions: {end_time - start_time:.2f} seconds")

y_true = 11.29 #import from the reference
y_pred =price
print(f"Mean Absolute Error Percentage (MAEP): {maep:.2f}%")

"""d=5, S0=110"""

# Parameters
d = 5  #dimension of GBM
mu = (r - dividend) * np.ones(shape = (d, )) # drift
sigma = 0.2 * np.ones(shape = (d, )) #volatility
S0 = 110.0 * np.ones(shape = (d, )) #initial price
# we use lessp paths due to limited computational resources
base_steps = 1500 #number of training steps regardless of the dimension
total_paths = batch_size * (base_steps + d) #total number of paths, base_steps+d is the training steps, where each step we need 8192 paths
number_of_training_steps = int(total_paths / batch_size) #number of training steps

#Simulating GBM paths
X = GBM(d, mu, sigma, S0, T, dt, total_paths, seed=seed_train) #simulating GBM paths
X = X.reshape(base_steps+d, batch_size, d, 10) #reshaping to (1500+d, 8192, d, 10)

# training the model for each stopping time
start_time = time.time()  # Start the timer
for i in range(N-1, 0, -1):
    model = create_model(d).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr = learning_rate)
    train(X, r, k, dt, model, i, optimizer, number_of_training_steps, batch_size)
    # store the model and the stopping decision function and stopping time function
    F[i] = model
    f[i] = lambda x, i=i: fi(x, i, F) #  store the stopping decision function
    l[i] = lambda x, i=i: li(x, i, f, l) # store the stopping time function
end_time = time.time()  # End the timer

# test the model on new set of paths
X = GBM(d, mu, sigma, S0, T, dt, test_paths, seed = seed_test)
g_val = g(X, r, k, dt) # g values at stopping times
X = torch.from_numpy(X).float().to(device) # convert X to a tensor
Z = g_val[range(test_paths), l[1](X)] # g values at stopping times, l[1]
price = 1 / test_paths * np.sum(Z) # monte carlo estimate of the price
print(f"Estimated price of the {d}D Bermudan Max Call Option: {price:.3f}")
print(f"Time for training the model with {d} dimensions: {end_time - start_time:.2f} seconds")

y_true = 36.710 #import from the reference
y_pred =price
print(f"Mean Absolute Error Percentage (MAEP): {maep:.2f}%")

"""d=10,S0=90"""

# Parameters
d = 10 #dimension of GBM
mu = (r - dividend) * np.ones(shape = (d, )) # drift
sigma = 0.2 * np.ones(shape = (d, )) #volatility
S0 = 90.0 * np.ones(shape = (d, )) #initial price
# we use lessp paths due to limited computational resources
base_steps = 1500 #number of training steps regardless of the dimension
total_paths = batch_size * (base_steps + d) #total number of paths, base_steps+d is the training steps, where each step we need 8192 paths
number_of_training_steps = int(total_paths / batch_size) #number of training steps

#Simulating GBM paths
X = GBM(d, mu, sigma, S0, T, dt, total_paths, seed=seed_train) #simulating GBM paths
X = X.reshape(base_steps+d, batch_size, d, 10) #reshaping to (1500+d, 8192, d, 10)

# training the model for each stopping time
start_time = time.time()  # Start the timer
for i in range(N-1, 0, -1):
    model = create_model(d).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr = learning_rate)
    train(X, r, k, dt, model, i, optimizer, number_of_training_steps, batch_size)
    # store the model and the stopping decision function and stopping time function
    F[i] = model
    f[i] = lambda x, i=i: fi(x, i, F) #  store the stopping decision function
    l[i] = lambda x, i=i: li(x, i, f, l) # store the stopping time function
end_time = time.time()  # End the timer

# test the model on new set of paths
X = GBM(d, mu, sigma, S0, T, dt, test_paths, seed = seed_test)
g_val = g(X, r, k, dt) # g values at stopping times
X = torch.from_numpy(X).float().to(device) # convert X to a tensor
Z = g_val[range(test_paths), l[1](X)] # g values at stopping times, l[1]
price = 1 / test_paths * np.sum(Z) # monte carlo estimate of the price
print(f"Estimated price of the {d}D Bermudan Max Call Option: {price:.3f}")
print(f"Time for training the model with {d} dimensions: {end_time - start_time:.2f} seconds")

y_true = 26.208 #import from the reference
y_pred =price
print(f"Mean Absolute Error Percentage (MAEP): {maep:.2f}%")