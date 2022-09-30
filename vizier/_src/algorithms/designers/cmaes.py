"""CMA-ES designer."""

import queue
from typing import Optional, Sequence

from evojax.algo import cma_jax
import jax.numpy as jnp
import numpy as np

from vizier import algorithms as vza
from vizier import pyvizier as vz
from vizier.pyvizier import converters


# TODO: Use a Partially Serializable Designer instead to capture
# CMA-ES _State, which contains all jnp.ndarray's.
class CMAESDesigner(vza.Designer):
  """CMA-ES designer wrapping evo-jax.

  NOTE: Since the base version of CMA-ES expects the entire population size to
  be evaluated before an update, we must use temporary queues to hold partially
  finished populations.
  """

  def __init__(self, problem_statement: vz.ProblemStatement, **cma_kwargs):
    """Init.

    Args:
      problem_statement: Must use a flat DOUBLE-only search space.
      **cma_kwargs: Keyword arguments for the CMA_ES_JAX class.
    """
    self._problem_statement = problem_statement
    self._metric_name = self._problem_statement.metric_information.item().name

    self._search_space = self._problem_statement.search_space
    if self._search_space.is_conditional:
      raise ValueError(
          f'This designer {self} does not support conditional search.')
    for parameter_config in self._search_space.parameters:
      if parameter_config.type != vz.ParameterType.DOUBLE:
        raise ValueError(
            f'This designer {self} only supports continuous parameters.')
    self._num_params = len(self._search_space.parameters)
    if self._num_params < 2:
      raise ValueError(
          f'CMA-ES only supports search spaces with >=2 parameters. Current number of parameters: {self._num_params}'
      )

    self._converter = converters.TrialToArrayConverter.from_study_config(
        self._problem_statement)
    self._cma_es_jax = cma_jax.CMA_ES_JAX(
        param_size=self._num_params, **cma_kwargs)
    self._trial_population = queue.Queue(
        maxsize=self._cma_es_jax.hyper_parameters.pop_size)

  def update(self, trials: vza.CompletedTrials) -> None:
    completed_trials = list(trials.completed)

    # Keep inserting completed trials into population. If population is full,
    # a CMA-ES update and queue clear are triggered.
    while completed_trials:
      self._trial_population.put(completed_trials.pop())

      if self._trial_population.full():
        # Once full, make a full CMA-ES update.
        features, labels = self._converter.to_xy(
            list(self._trial_population.queue))
        if self._problem_statement.metric_information.item(
        ) == vz.ObjectiveMetricGoal.MINIMIZE:
          # CMA-ES expects a maximization problem by default.
          labels = -labels
        # CMA-ES expects fitness to be shape (pop_size,) and solutions of shape
        # (pop_size, num_params).
        self._cma_es_jax.tell(
            fitness=jnp.array(labels[:, 0]), solutions=jnp.array(features))
        self._trial_population.queue.clear()

  def suggest(self,
              count: Optional[int] = None) -> Sequence[vz.TrialSuggestion]:
    """Make new suggestions.

    Args:
      count: Makes best effort to generate this many suggestions. If None,
        suggests as many as the algorithm wants.

    Returns:
      New suggestions.
    """
    count = count or 1
    cma_suggestions = np.array(self._cma_es_jax.ask(count))

    # Convert CMA suggestions to suggestions.
    return [
        vz.TrialSuggestion(params)
        for params in self._converter.to_parameters(cma_suggestions)
    ]