"""Bridge guards and learned-field preservation; no historical model is loaded."""
import unittest

from dreamm.model_compat import OLD_NODE_FIELDS, extend_nodes, load_original, predict_original

try:
    import numpy as np
    import pandas as pd
except ImportError:
    np = pd = None


class ModelIdentityTests(unittest.TestCase):
    def test_arbitrary_pickle_rejected_before_import_or_deserialization(self):
        for raw in (None, bytearray(b'x'), b'', b'\x80\x03invalid', b'x' * 544460):
            with self.subTest(kind=type(raw).__name__):
                with self.assertRaisesRegex(ValueError, 'hash-pinned'):
                    load_original(raw)


@unittest.skipIf(np is None or pd is None, 'requires the separately installed modern numeric profile')
class TreeAndPredictionTests(unittest.TestCase):
    def nodes(self):
        old_dtype = np.dtype([(key, '<i8' if key in ('left_child', 'right_child', 'feature', 'n_node_samples') else '<f8')
                              for key in OLD_NODE_FIELDS])
        nodes = np.zeros(3, dtype=old_dtype)
        nodes['left_child'] = [1, -1, -1]
        nodes['right_child'] = [2, -1, -1]
        nodes['feature'] = [13, -2, -2]
        nodes['threshold'] = [0.125, -2.0, -2.0]
        nodes['n_node_samples'] = [20, 12, 8]
        nodes['weighted_n_node_samples'] = [20.0, 12.0, 8.0]
        return nodes, np.dtype([*old_dtype.descr, ('missing_go_to_left', 'u1')], align=True)

    def test_old_thresholds_topology_and_sample_counts_preserved_exactly(self):
        nodes, target = self.nodes()
        before = nodes.tobytes()
        converted = extend_nodes(nodes, target)
        self.assertEqual(nodes.tobytes(), before)
        for field in OLD_NODE_FIELDS:
            np.testing.assert_array_equal(nodes[field], converted[field])
        np.testing.assert_array_equal(converted['missing_go_to_left'], [0, 0, 0])

    def test_unreviewed_old_or_new_node_schema_fails_closed(self):
        nodes, target = self.nodes()
        with self.assertRaisesRegex(ValueError, 'original tree'):
            extend_nodes(np.zeros(1, dtype=[('foreign', 'i8')]), target)
        with self.assertRaisesRegex(ValueError, 'modern tree'):
            extend_nodes(nodes, nodes.dtype)

    def matrix(self):
        names = tuple('f' + str(i) for i in range(727))
        return names, pd.DataFrame(np.zeros((2, 727)), columns=names)

    def test_real_predict_method_invoked_on_exact_feature_order(self):
        names, matrix = self.matrix()
        class Model:
            def predict(self, value):
                self.observed = value
                return np.array([1, 0])
        model = Model()
        np.testing.assert_array_equal(predict_original(model, matrix, names), [1, 0])
        self.assertIs(model.observed, matrix)

    def test_reordered_or_nonfinite_inputs_cannot_reach_model(self):
        names, matrix = self.matrix()
        class NeverPredict:
            def predict(self, value):
                raise AssertionError('invalid input reached model')
        with self.assertRaisesRegex(ValueError, 'ordered'):
            predict_original(NeverPredict(), matrix, names[::-1])
        for invalid in (np.nan, np.inf, -np.inf):
            matrix.iloc[0, 0] = invalid
            with self.assertRaisesRegex(ValueError, 'nonfinite'):
                predict_original(NeverPredict(), matrix, names)

    def test_invalid_label_or_shape_is_rejected(self):
        names, matrix = self.matrix()
        for value in (np.array([2, 0]), np.array([[1], [0]]), np.array([1])):
            class Model:
                def predict(self, X):
                    return value
            with self.assertRaisesRegex(ValueError, 'classifier output'):
                predict_original(Model(), matrix, names)
