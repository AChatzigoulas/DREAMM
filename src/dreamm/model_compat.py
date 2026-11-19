"""In-memory serialization bridge for the one immutable DREAMM classifier.

No training, model export, global sklearn monkeypatch or learned-value edits.
Only finite 727-column inputs are supported; NaN-routing fields are unused.
Cross-version support is deliberately limited to the pinned model and tests.
"""
import hashlib
import io
from dreamm_m1.measurement import measured

MODEL_SHA256 = 'b68b031bce885a2b7f572205a42ff0986947b1e62a788dbde409a5e0b379b90d'
OLD_NODE_FIELDS = ('left_child', 'right_child', 'feature', 'threshold', 'impurity',
                   'n_node_samples', 'weighted_n_node_samples')


def extend_nodes(nodes, new_dtype):
    """Change storage layout only; retain every original learned field."""
    import numpy as np
    if nodes.dtype.names != OLD_NODE_FIELDS:
        raise ValueError('unexpected original tree node schema')
    if tuple(new_dtype.names) != (*OLD_NODE_FIELDS, 'missing_go_to_left'):
        raise ValueError('unreviewed modern tree schema')
    extended = np.zeros(nodes.shape, dtype=new_dtype)
    for name in OLD_NODE_FIELDS:
        extended[name] = nodes[name]
        if not np.array_equal(extended[name], nodes[name]):
            raise ValueError('learned tree field changed during representation bridge')
    return extended


@measured('inference.original_model_load')
def load_original(raw):
    if not isinstance(raw, bytes) or len(raw) != 544460 or hashlib.sha256(raw).hexdigest() != MODEL_SHA256:
        raise ValueError('only the original hash-pinned DREAMM model can be loaded')
    import numpy as np
    from joblib.numpy_pickle import NumpyUnpickler
    from sklearn.tree import _tree, DecisionTreeClassifier, ExtraTreeClassifier
    from sklearn.base import BaseEstimator
    from lightgbm import LGBMClassifier
    changes = []
    new_dtype = _tree.Tree(1, np.array([2], dtype=np.intp), 1).__getstate__()['nodes'].dtype

    class CompatibleTree(_tree.Tree):
        def __setstate__(self, state):
            nodes = state['nodes']
            extended = extend_nodes(nodes, new_dtype)
            # The original tree had no missing-value routing. predict() below
            # rejects nonfinite data before any tree can observe this field.
            extended['missing_go_to_left'] = 0
            super().__setstate__({**state, 'nodes': extended})
            if not np.array_equal(self.__getstate__()['values'], state['values']):
                raise ValueError('learned leaf values changed')
            changes.append({'tree_nodes': len(nodes), 'old_fields_preserved': True,
                            'leaf_values_preserved': True, 'unused_nan_field': 0})

    class CountsProbability:
        def __setstate__(self, state):
            self.__dict__.update(state)
            if 'n_features_in_' not in self.__dict__ and 'n_features_' in state:
                self.n_features_in_ = state['n_features_']
            self.monotonic_cst = None  # the original trees were unconstrained

        def predict_proba(self, X, check_input=True):
            # sklearn 0.22 stored counts, not the proportions expected by
            # sklearn >=1.4. Normalize at prediction, retaining original values.
            X = self._validate_X_predict(X, check_input)
            values = self.tree_.predict(X)
            if self.n_outputs_ != 1:
                raise ValueError('the pinned DREAMM classifier requires single-output trees')
            values = values[:, :self.n_classes_]
            normalizer = values.sum(axis=1)[:, np.newaxis]
            normalizer[normalizer == 0.0] = 1.0
            return values / normalizer

    class CompatibleDecisionTree(CountsProbability, DecisionTreeClassifier):
        pass

    class CompatibleExtraTree(CountsProbability, ExtraTreeClassifier):
        pass

    class PinnedUnpickler(NumpyUnpickler):
        def find_class(self, module, name):
            if (module, name) == ('sklearn.tree._tree', 'Tree'):
                return CompatibleTree
            if module in ('sklearn.tree._classes', 'sklearn.tree.tree'):
                if name == 'DecisionTreeClassifier':
                    return CompatibleDecisionTree
                if name == 'ExtraTreeClassifier':
                    return CompatibleExtraTree
            return super().find_class(module, name)

    model = PinnedUnpickler('original-dreamm-model.sav', io.BytesIO(raw), True).load()
    # n_features_ was renamed; copying this identity field does not refit or
    # alter any learned parameter. Walk only estimator containers, not arrays.
    seen = set()
    def adapt(value):
        if id(value) in seen or isinstance(value, (str, bytes, int, float)):
            return
        seen.add(id(value))
        if isinstance(value, np.ndarray):
            if value.dtype == object:
                for item in value.flat: adapt(item)
        elif isinstance(value, dict):
            for item in value.values(): adapt(item)
        elif isinstance(value, (list, tuple)):
            for item in value: adapt(item)
        elif isinstance(value, BaseEstimator) or type(value).__module__.startswith('mlxtend.classifier'):
            state = value.__dict__
            if isinstance(value, LGBMClassifier):
                # Old LightGBM uses _Booster presence as its fitted check. The
                # current wrapper requires explicit identity-only attributes.
                booster = state.get('_Booster')
                if booster is None or booster.num_feature() != 727 or state.get('_n_features') != 727:
                    raise ValueError('unexpected original LightGBM feature identity')
                if state.get('_n_classes') != 2 or not np.array_equal(state.get('_classes'), [0, 1]):
                    raise ValueError('unexpected original LightGBM class identity')
                state['_n_features_in'] = 727
                state['fitted_'] = True
                changes.append({'lightgbm_fitted_identity': True, 'feature_count': 727,
                                'booster_modified': False})
            if 'n_features_' in state and 'n_features_in_' not in state:
                state['n_features_in_'] = state['n_features_']
            for item in tuple(state.values()): adapt(item)
    adapt(model)
    return model, {'schema': 'dreamm-original-model-bridge/1', 'model_sha256': MODEL_SHA256,
                   'serialized_model_modified': False, 'trees': changes}


@measured('inference.classifier')
def predict_original(model, matrix, ordered_names):
    import numpy as np
    if tuple(matrix.columns) != tuple(ordered_names) or matrix.shape[1] != 727:
        raise ValueError('exact ordered classifier feature schema required')
    if not np.isfinite(matrix.to_numpy(dtype=float)).all():
        raise ValueError('original model bridge does not support nonfinite inputs')
    labels = model.predict(matrix)
    if labels.shape != (len(matrix),) or not np.isin(labels, (0, 1)).all():
        raise ValueError('invalid original classifier output')
    return labels
