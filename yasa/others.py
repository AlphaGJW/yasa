"""
This file contains several helper functions to manipulate 1D and 2D EEG data.
"""
import numpy as np
from scipy.interpolate import interp1d
from .numba import _slope_lstsq, _covar, _corr, _rms

__all__ = ['moving_transform', 'trimbothstd', 'sliding_window',
           'get_centered_indices']


def moving_transform(x, y=None, sf=100, window=.3, step=.1, method='corr',
                     interp=False):
    """Moving transformation of one or two time-series.

    Parameters
    ----------
    x : array_like
        Single-channel data
    y : array_like, optional
        Second single-channel data (only used if method in ['corr', 'covar']).
    sf : float
        Sampling frequency.
    window : int
        Window size in seconds.
    step : int
        Step in seconds.
        A step of 0.1 second (100 ms) is usually a good default.
        If step == 0, overlap at every sample (slowest)
        If step == nperseg, no overlap (fastest)
        Higher values = higher precision = slower computation.
    method : str
        Transformation to use.
        Available methods are::

            'mean' : arithmetic mean of x
            'min' : minimum value of x
            'max' : maximum value of x
            'ptp' : peak-to-peak amplitude of x
            'prop_above_zero' : proportion of values of x that are above zero
            'rms' : root mean square of x
            'slope' : slope of the least-square regression of x (in a.u / sec)
            'corr' : Correlation between x and y
            'covar' : Covariance between x and y
    interp : boolean
        If True, a cubic interpolation is performed to ensure that the output
        has the same size as the input.

    Returns
    -------
    t : np.array
        Time vector, in seconds, corresponding to the MIDDLE of each epoch.
    out : np.array
        Transformed signal

    Notes
    -----
    This function was inspired by the `transform_signal` function of the
    Wonambi package (https://github.com/wonambi-python/wonambi).
    """
    # Safety checks
    assert method in ['mean', 'min', 'max', 'ptp', 'rms',
                      'prop_above_zero', 'slope', 'covar', 'corr']
    x = np.asarray(x, dtype=np.float64)
    if y is not None:
        y = np.asarray(y, dtype=np.float64)
        assert x.size == y.size

    if step == 0:
        step = 1 / sf

    halfdur = window / 2
    n = x.size
    total_dur = n / sf
    last = n - 1
    idx = np.arange(0, total_dur, step)
    out = np.zeros(idx.size)

    # Define beginning, end and time (centered) vector
    beg = ((idx - halfdur) * sf).astype(int)
    end = ((idx + halfdur) * sf).astype(int)
    beg[beg < 0] = 0
    end[end > last] = last
    # Alternatively, to cut off incomplete windows (comment the 2 lines above)
    # mask = ~((beg < 0) | (end > last))
    # beg, end = beg[mask], end[mask]
    t = np.column_stack((beg, end)).mean(1) / sf

    if method == 'mean':
        def func(x):
            return np.mean(x)

    elif method == 'min':
        def func(x):
            return np.min(x)

    elif method == 'max':
        def func(x):
            return np.max(x)

    elif method == 'ptp':
        def func(x):
            return np.ptp(x)

    elif method == 'prop_above_zero':
        def func(x):
            return np.count_nonzero(x >= 0) / x.size

    elif method == 'slope':
        def func(x):
            times = np.arange(x.size, dtype=np.float64) / sf
            return _slope_lstsq(times, x)

    elif method == 'covar':
        def func(x, y):
            return _covar(x, y)

    elif method == 'corr':
        def func(x, y):
            return _corr(x, y)

    else:
        def func(x):
            return _rms(x)

    # Now loop over successive epochs
    if method in ['covar', 'corr']:
        for i in range(idx.size):
            out[i] = func(x[beg[i]:end[i]], y[beg[i]:end[i]])
    else:
        for i in range(idx.size):
            out[i] = func(x[beg[i]:end[i]])

    # Finally interpolate
    if interp and step != 1 / sf:
        f = interp1d(t, out, kind='cubic', bounds_error=False,
                     fill_value=0, assume_sorted=True)
        t = np.arange(n) / sf
        out = f(t)

    return t, out


def _zerocrossings(x):
    """Find indices of zero-crossings in a 1D array.

    Parameters
    ----------
    x : np.array
        One dimensional data vector.

    Returns
    -------
    idx_zc : np.array
        Indices of zero-crossings

    Examples
    --------

        >>> import numpy as np
        >>> from yasa.main import _zerocrossings
        >>> a = np.array([4, 2, -1, -3, 1, 2, 3, -2, -5])
        >>> _zerocrossings(a)
            array([1, 3, 6], dtype=int64)
    """
    pos = x > 0
    npos = ~pos
    return ((pos[:-1] & npos[1:]) | (npos[:-1] & pos[1:])).nonzero()[0]


def trimbothstd(x, cut=0.10):
    """
    Slices off a proportion of items from both ends of an array and then
    compute the sample standard deviation.

    Slices off the passed proportion of items from both ends of the passed
    array (i.e., with ``cut`` = 0.1, slices leftmost 10% **and**
    rightmost 10% of scores). The trimmed values are the lowest and
    highest ones.
    Slices off less if proportion results in a non-integer slice index.

    Parameters
    ----------
    x : 1D np.array
        Input array.
    cut : float
        Proportion (in range 0-1) of total data to trim of each end.
        Default is 0.10, i.e. 10% lowest and 10% highest values are removed.

    Returns
    -------
    trimmed_std : float
        Sample standard deviation of the trimmed array.
    """
    x = np.asarray(x)
    n = x.size
    lowercut = int(cut * n)
    uppercut = n - lowercut
    atmp = np.partition(x, (lowercut, uppercut - 1))
    sl = slice(lowercut, uppercut)
    return atmp[sl].std(ddof=1)


def sliding_window(data, sf, window, step=None, axis=-1):
    """
    Calculate a sliding window of a 1D or 2D EEG signal.

    .. versionadded:: 0.1.7

    Parameters
    ----------
    data : numpy array
        The 1D or 2D EEG data.
    sf : float
        The sampling frequency of ``data``.
    window : int
        The sliding window length, in seconds.
    step : int
        The sliding window step length, in seconds.
        If None (default), ``step`` is set to ``window``,
        which results in no overlap between the sliding windows.
    axis : int
        The axis to slide over. Defaults to the last axis.

    Returns
    -------
    times : numpy array
        Time vector, in seconds, corresponding to the START of each sliding
        epoch in ``strided``.
    strided : numpy array
        A matrix where row in last dimension consists of one instance
        of the sliding window.

    Notes
    -----
    This is a wrapper around the
    :py:func:`numpy.lib.stride_tricks.as_strided` function.
    """
    from numpy.lib.stride_tricks import as_strided
    assert axis <= data.ndim, "Axis value out of range."
    assert isinstance(sf, (int, float)), 'sf must be int or float'
    assert isinstance(window, (int, float)), 'window must be int or float'
    assert isinstance(step, (int, float, type(None))), ('step must be int, '
                                                        'float or None.')
    if isinstance(sf, float):
        assert sf.is_integer(), 'sf must be a whole number.'
        sf = int(sf)
    assert isinstance(axis, int), 'axis must be int.'

    # window and step in samples instead of points
    window *= sf
    step = window if step is None else step * sf

    if isinstance(window, float):
        assert window.is_integer(), 'window * sf must be a whole number.'
        window = int(window)

    if isinstance(step, float):
        assert step.is_integer(), 'step * sf must be a whole number.'
        step = int(step)

    assert step >= 1, "Stepsize may not be zero or negative."
    assert window < data.shape[axis], ("Sliding window size may not exceed "
                                       "size of selected axis")

    shape = list(data.shape)
    shape[axis] = np.floor(data.shape[axis] / step - window / step + 1
                           ).astype(int)
    shape.append(window)

    strides = list(data.strides)
    strides[axis] *= step
    strides.append(data.strides[axis])

    strided = as_strided(data, shape=shape, strides=strides)
    t = np.arange(strided.shape[-2]) * (step / sf)
    return t, strided


def get_centered_indices(data, idx, npts_before, npts_after):
    """Get a 2D array of indices in data centered around specific time points,
    automatically excluding indices that are outside the bounds of data.

    Parameters
    ----------
    data : 1-D array_like
        Input data.
    idx : 1-D array_like
        Indices of events in data (e.g. peaks)
    npts_before : int
        Number of data points to include before ``idx``
    npts_after : int
        Number of data points to include after ``idx``

    Returns
    -------
    idx_ep : 2-D array
        Array of indices of shape (len(idx_nomask), npts_before +
        npts_after + 1). Indices outside the bounds of data are removed.
    idx_nomask : 1-D array
        Indices of ``idx`` that are not masked (= valid).

    Examples
    --------
    >>> import numpy as np
    >>> from yasa import get_centered_indices
    >>> np.random.seed(123)
    >>> data = np.random.normal(size=100).round(2)
    >>> idx = [1., 10., 20., 30., 50., 102]
    >>> before, after = 3, 2
    >>> idx_ep, idx_nomask = get_centered_indices(data, idx, before, after)
    >>> idx_ep
    array([[ 7,  8,  9, 10, 11, 12],
           [17, 18, 19, 20, 21, 22],
           [27, 28, 29, 30, 31, 32],
           [47, 48, 49, 50, 51, 52]])

    >>> data[idx_ep]
    array([[-0.43,  1.27, -0.87, -0.68, -0.09,  1.49],
           [ 2.19,  1.  ,  0.39,  0.74,  1.49, -0.94],
           [-1.43, -0.14, -0.86, -0.26, -2.8 , -1.77],
           [ 0.41,  0.98,  2.24, -1.29, -1.04,  1.74]])

    >>> idx_nomask
    array([1, 2, 3, 4], dtype=int64)
    """
    # Safety check
    assert isinstance(npts_before, (int, float))
    assert isinstance(npts_after, (int, float))
    assert float(npts_before).is_integer()
    assert float(npts_after).is_integer()
    npts_before = int(npts_before)
    npts_after = int(npts_after)
    data = np.asarray(data)
    idx = np.asarray(idx, dtype='int')
    assert idx.ndim == 1, "idx must be 1D."
    assert data.ndim == 1, "data must be 1D."

    def rng(x):
        """Create a range before and after a given value."""
        return np.arange(x - npts_before, x + npts_after + 1, dtype='int')

    n_peaks = idx.shape[0]
    idx_ep = np.apply_along_axis(rng, 1, idx[..., np.newaxis])
    # We drop the events for which the indices exceed data
    idx_ep = np.ma.mask_rows(np.ma.masked_outside(idx_ep, 0, data.shape[0]))
    # Indices of non-masked epochs in idx
    idx_ep_nomask = np.unique(idx_ep.nonzero()[0])
    idx_ep = np.ma.compress_rows(idx_ep)
    return idx_ep, idx_ep_nomask
