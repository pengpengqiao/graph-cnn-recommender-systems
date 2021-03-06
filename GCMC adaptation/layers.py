from __future__ import print_function


from initializations import *
import tensorflow as tf

# global unique layer ID dictionary for layer name assignment
_LAYER_UIDS = {}


def dot(x, y, sparse=False):
    """Wrapper for tf.matmul (sparse vs dense)."""
    if sparse:
        res = tf.sparse_tensor_dense_matmul(x, y)
    else:
        res = tf.matmul(x, y)
    return res


def get_layer_uid(layer_name=''):
    """Helper function, assigns unique layer IDs
    """
    if layer_name not in _LAYER_UIDS:
        _LAYER_UIDS[layer_name] = 1
        return 1
    else:
        _LAYER_UIDS[layer_name] += 1
        return _LAYER_UIDS[layer_name]


def dropout_sparse(x, keep_prob, num_nonzero_elems):
    """Dropout for sparse tensors. Currently fails for very large sparse tensors (>1M elements)
    """
    noise_shape = [num_nonzero_elems]
    random_tensor = keep_prob
    random_tensor += tf.random_uniform(noise_shape)
    dropout_mask = tf.cast(tf.floor(random_tensor), dtype=tf.bool)
    pre_out = tf.sparse_retain(x, dropout_mask)

    return pre_out * tf.div(1., keep_prob)


class Layer(object):
    """Base layer class. Defines basic API for all layer objects.
    # Properties
        name: String, defines the variable scope of the layer.
            Layers with common name share variables. (TODO)
        logging: Boolean, switches Tensorflow histogram logging on/off
    # Methods
        _call(inputs): Defines computation graph of layer
            (i.e. takes input, returns output)
        __call__(inputs): Wrapper for _call()
        _log_vars(): Log all variables
    """

    def __init__(self, **kwargs):
        allowed_kwargs = {'name', 'logging'}
        for kwarg in kwargs.keys():
            assert kwarg in allowed_kwargs, 'Invalid keyword argument: ' + kwarg
        name = kwargs.get('name')
        if not name:
            layer = self.__class__.__name__.lower()
            name = layer + '_' + str(get_layer_uid(layer))
        self.name = name
        self.vars = {}
        logging = kwargs.get('logging', False)
        self.logging = logging
        self.sparse_inputs = False

    def _call(self, inputs):
        return inputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs', inputs)
            outputs = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs', outputs)
            return outputs

    def _log_vars(self):
        for var in self.vars:
            tf.summary.histogram(self.name + '/vars/' + var, self.vars[var])


class Dense(Layer):
    """Dense layer for two types of nodes in a bipartite graph. """

    def __init__(self, input_dim, output_dim, dropout=0., act=tf.nn.relu, share_user_item_weights=False,
                 bias=False, **kwargs):
        super(Dense, self).__init__(**kwargs)

        with tf.variable_scope(self.name + '_vars'):
            if not share_user_item_weights:

                self.vars['weights_u'] = weight_variable_random_uniform(input_dim, output_dim, name="weights_u")
                self.vars['weights_v'] = weight_variable_random_uniform(input_dim, output_dim, name="weights_v")

                if bias:
                    self.vars['user_bias'] = bias_variable_truncated_normal([output_dim], name="bias_u")
                    self.vars['item_bias'] = bias_variable_truncated_normal([output_dim], name="bias_v")


            else:
                self.vars['weights_u'] = weight_variable_random_uniform(input_dim, output_dim, name="weights")
                self.vars['weights_v'] = self.vars['weights_u']

                if bias:
                    self.vars['user_bias'] = bias_variable_truncated_normal([output_dim], name="bias_u")
                    self.vars['item_bias'] = self.vars['user_bias']

        self.bias = bias

        self.dropout = dropout
        self.act = act
        if self.logging:
            self._log_vars()

    def _call(self, inputs):
        x_u = inputs[0]
        x_u = tf.nn.dropout(x_u, 1 - self.dropout)
        x_u = tf.matmul(x_u, self.vars['weights_u'])

        x_v = inputs[1]
        x_v = tf.nn.dropout(x_v, 1 - self.dropout)
        x_v = tf.matmul(x_v, self.vars['weights_v'])

        u_outputs = self.act(x_u)
        v_outputs = self.act(x_v)

        if self.bias:
            u_outputs += self.vars['user_bias']
            v_outputs += self.vars['item_bias']

        return u_outputs, v_outputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging:
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class OrdinalRGGCN(Layer):
    """Residual gated graph convolutional layer (Bresson). adapted from stackGC layer """
    def __init__(self, input_dim, output_dim, E_start_list, E_end_list, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(OrdinalRGGCN, self).__init__(**kwargs)

        assert len(E_start_list) == num_support, 'length of E_start not equal to num_support'

        self.sparse_inputs = sparse_inputs

        with tf.variable_scope(self.name + '_vars'):
            self.Ui1 = tf.stack([weight_variable_random_uniform(input_dim, output_dim, name='Ui1_%d' % i) for i in range(num_support)], axis=0)
            self.Uj1 = tf.stack([weight_variable_random_uniform(input_dim, output_dim, name='Uj1_%d' % i) for i in range(num_support)], axis=0)
            self.Vi1 = tf.stack([weight_variable_random_uniform(input_dim, output_dim, name='Vi1_%d' % i) for i in range(num_support)], axis=0)
            self.Vj1 = tf.stack([weight_variable_random_uniform(input_dim, output_dim, name='Vj1_%d' % i) for i in range(num_support)], axis=0)
            self.bu1 = bias_variable_zero([output_dim], name="bu1")
            self.bv1 = bias_variable_zero([output_dim], name="bv1")

            self.Ui2 = tf.stack([weight_variable_random_uniform(output_dim, output_dim, name='Ui2_%d' % i) for i in range(num_support)], axis=0)
            self.Uj2 = tf.stack([weight_variable_random_uniform(output_dim, output_dim, name='Uj2_%d' % i) for i in range(num_support)], axis=0)
            self.Vi2 = tf.stack([weight_variable_random_uniform(output_dim, output_dim, name='Vi2_%d' % i) for i in range(num_support)], axis=0)
            self.Vj2 = tf.stack([weight_variable_random_uniform(output_dim, output_dim, name='Vj2_%d' % i) for i in range(num_support)], axis=0)
            self.bu2 = bias_variable_zero([output_dim], name="bu2")
            self.bv2 = bias_variable_zero([output_dim], name="bv2")
            
            # resnet
            self.R = weight_variable_random_uniform(input_dim, output_dim, name='R')

        self.dropout = dropout
        self.act = act

        self.E_start = E_start_list
        self.E_end = E_end_list

        if self.logging:
            self._log_vars()

    def get_weight_variable(self, input_dim, output_dim, num_support, name):
        var = weight_variable_random_uniform(input_dim, output_dim, name=name)
        var = tf.split(value=var, axis=1, num_or_size_splits=num_support)
        return var

    def get_bias_variable(self, output_dim, num_support, name):
        var = bias_variable_zero(output_dim, name=name)
        var = tf.split(value=var, axis=0, num_or_size_splits=num_support)
        return var

    def _call(self, inputs):
        num_users = inputs[0].dense_shape[0]
        num_items = inputs[1].dense_shape[0]
        users = tf.sparse_to_dense(inputs[0].indices, inputs[0].dense_shape, inputs[0].values)
        items = tf.sparse_to_dense(inputs[1].indices, inputs[1].dense_shape, inputs[1].values)
        original_x = tf.concat([users, items], axis=0)  # CHECK THIS! need to combine users and items into one single array. becomes 6000 (users+items) x 6000 (input_dim)
        original_x = tf.nn.dropout(original_x, 1-self.dropout)
        
        outputs = []
        Ui1 = 0.
        Uj1 = 0.
        Vi1 = 0.
        Vj1 = 0.
        for i in range(len(self.E_start)):
            Ui1 += self.Ui1[i]
            Uj1 += self.Uj1[i]
            Vi1 += self.Vi1[i]
            Vj1 += self.Vj1[i]
            # E_start, E_end : E x V
            x = original_x
            # conv1
            Vix = dot(x, Vi1)  # Vi1[i] is 6000x100
            Vjx = dot(x, Vj1)
            x1 = tf.add(dot(self.E_end[i], Vix, sparse=True), dot(self.E_start[i], Vjx, sparse=True))
            x1 = tf.nn.bias_add(x1, self.bv1)
            x1 = tf.nn.sigmoid(x1)
            Uix = dot(x, Ui1)
            Ujx = dot(x, Uj1)
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), tf.multiply(x1, x2), sparse=True))
            x = tf.nn.bias_add(x, self.bu1)
            x = tf.layers.batch_normalization(x)
            x = tf.nn.relu(x)
            outputs.append(x)
        output = tf.add_n(outputs)

        outputs = []
        Ui2 = 0.
        Uj2 = 0.
        Vi2 = 0.
        Vj2 = 0.
        for i in range(len(self.E_start)):
            Ui2 += self.Ui2[i]
            Uj2 += self.Uj2[i]
            Vi2 += self.Vi2[i]
            Vj2 += self.Vj2[i]

            x = output
            # conv2
            Vix = dot(x, Vi2)
            Vjx = dot(x, Vj2)
            x1 = tf.add(dot(self.E_end[i], Vix, sparse=True), dot(self.E_start[i], Vjx, sparse=True))
            x1 = tf.nn.bias_add(x1, self.bv1)
            x1 = tf.nn.sigmoid(x1)
            Uix = dot(x, Ui2)
            Ujx = dot(x, Uj2)
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), tf.multiply(x1, x2), sparse=True))
            x = tf.nn.bias_add(x, self.bu1)
            x = tf.layers.batch_normalization(x)
            outputs.append(x)

        output = tf.add_n(outputs)
        output = tf.add(output, tf.matmul(original_x, self.R))
        output = tf.nn.relu(output)

        u = output[:tf.cast(num_users, tf.int32)]
        v = output[tf.cast(num_users, tf.int32):]

        return u, v

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs: # this will if tensors are sparse. sparse_inputs flag needs to be set properly.
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class StackRGGCN(Layer):
    """Residual gated graph convolutional layer (Bresson). adapted from stackGC layer """
    def __init__(self, input_dim, output_dim, E_start_list, E_end_list, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(StackRGGCN, self).__init__(**kwargs)

        assert output_dim % num_support == 0, 'output_dim must be multiple of num_support for stackGC layer'
        assert len(E_start_list) == num_support, 'length of E_start not equal to num_support'

        self.sparse_inputs = sparse_inputs

        with tf.variable_scope(self.name + '_vars'):
            # conv1 (with split weights)
            self.Ui1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Ui1')
            self.Uj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Uj1')
            self.Vi1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vi1')
            self.Vj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vj1')
            self.bu1 = self.get_bias_variable(output_dim, num_support, 'bu1')
            self.bv1 = self.get_bias_variable(output_dim, num_support, 'bv1')

            # resnet
            self.R = weight_variable_random_uniform(input_dim, output_dim, name='R')

        self.dropout = dropout
        self.act = act

        self.E_start = E_start_list
        self.E_end = E_end_list

        if self.logging:
            self._log_vars()

    def get_weight_variable(self, input_dim, output_dim, num_support, name):
        var = weight_variable_random_uniform(input_dim, output_dim, name=name)
        var = tf.split(value=var, axis=1, num_or_size_splits=num_support)
        return var

    def get_bias_variable(self, output_dim, num_support, name):
        var = bias_variable_zero(output_dim, name=name)
        var = tf.split(value=var, axis=0, num_or_size_splits=num_support)
        return var

    def _call(self, inputs):
        if self.sparse_inputs:
            num_users = inputs[0].dense_shape[0]
            num_items = inputs[1].dense_shape[0]
            users = tf.sparse_to_dense(inputs[0].indices, inputs[0].dense_shape, inputs[0].values)
            items = tf.sparse_to_dense(inputs[1].indices, inputs[1].dense_shape, inputs[1].values)
        else:
            num_users = inputs[0].shape[0]
            num_items = inputs[1].shape[0]
            users = inputs[0]
            items = inputs[1]
        
        original_x = tf.concat([users, items], axis=0)  # CHECK THIS! need to combine users and items into one single array. becomes 6000 (users+items) x 6000 (input_dim)
        original_x = tf.nn.dropout(original_x, 1-self.dropout)

        outputs = []
        for i in range(len(self.E_start)):
            # E_start, E_end : E x V
            x = original_x
            # conv1
            Vix = dot(x, self.Vi1[i])  # Vij[i] is 6000x100
            Vjx = dot(x, self.Vj1[i])
            x1 = tf.add(dot(self.E_end[i], Vix, sparse=True), dot(self.E_start[i], Vjx, sparse=True))
            x1 = tf.nn.bias_add(x1, self.bv1[i])
            x1 = tf.nn.sigmoid(x1)
            Uix = dot(x, self.Ui1[i])
            Ujx = dot(x, self.Uj1[i])
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), tf.multiply(x1, x2), sparse=True))
            x = tf.nn.bias_add(x, self.bu1[i])
            x = tf.layers.batch_normalization(x)
            x = tf.nn.relu(x)
            outputs.append(x)
        
        output = tf.concat(axis=1, values=outputs)
        output = tf.add(output, tf.matmul(original_x, self.R))
        output = tf.nn.relu(output)

        u = output[:tf.cast(num_users, tf.int32)]
        v = output[tf.cast(num_users, tf.int32):]

        return u, v

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs: # this will if tensors are sparse. sparse_inputs flag needs to be set properly.
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class StackRGGCNDouble(Layer):
    """Residual gated graph convolutional layer (Bresson). adapted from stackGC layer """
    def __init__(self, input_dim, output_dim, E_start_list, E_end_list, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(StackRGGCNDouble, self).__init__(**kwargs)

        assert output_dim % num_support == 0, 'output_dim must be multiple of num_support for stackGC layer'
        assert len(E_start_list) == num_support, 'length of E_start not equal to num_support'

        self.sparse_inputs = sparse_inputs

        with tf.variable_scope(self.name + '_vars'):
            # conv1 (with split weights)
            self.Ui1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Ui1')
            self.Uj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Uj1')
            self.Vi1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vi1')
            self.Vj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vj1')
            self.bu1 = self.get_bias_variable(output_dim, num_support, 'bu1')
            self.bv1 = self.get_bias_variable(output_dim, num_support, 'bv1')

            # conv2 (with split weights)
            self.Ui2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Ui2')
            self.Uj2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Uj2')
            self.Vi2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Vi2')
            self.Vj2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Vj2')
            self.bu2 = self.get_bias_variable(output_dim, num_support, 'bu2')
            self.bv2 = self.get_bias_variable(output_dim, num_support, 'bv2')
            
            # resnet
            self.R = weight_variable_random_uniform(input_dim, output_dim, name='R')

        self.dropout = dropout
        self.act = act

        self.E_start = E_start_list
        self.E_end = E_end_list

        if self.logging:
            self._log_vars()

    def get_weight_variable(self, input_dim, output_dim, num_support, name):
        var = weight_variable_random_uniform(input_dim, output_dim, name=name)
        var = tf.split(value=var, axis=1, num_or_size_splits=num_support)
        return var

    def get_bias_variable(self, output_dim, num_support, name):
        var = bias_variable_zero(output_dim, name=name)
        var = tf.split(value=var, axis=0, num_or_size_splits=num_support)
        return var

    def _call(self, inputs):
        if self.sparse_inputs:
            num_users = inputs[0].dense_shape[0]
            num_items = inputs[1].dense_shape[0]
            users = tf.sparse_to_dense(inputs[0].indices, inputs[0].dense_shape, inputs[0].values)
            items = tf.sparse_to_dense(inputs[1].indices, inputs[1].dense_shape, inputs[1].values)
        else:
            num_users = inputs[0].shape[0]
            num_items = inputs[1].shape[0]
            users = inputs[0]
            items = inputs[1]
        
        original_x = tf.concat([users, items], axis=0)  # CHECK THIS! need to combine users and items into one single array. becomes 6000 (users+items) x 6000 (input_dim)
        original_x = tf.nn.dropout(original_x, 1-self.dropout)

        outputs = []
        for i in range(len(self.E_start)):
            # E_start, E_end : E x V
            x = original_x
            # conv1
            Vix = dot(x, self.Vi1[i])  # Vij[i] is 6000x100
            Vjx = dot(x, self.Vj1[i])
            x1 = tf.add(dot(self.E_end[i], Vix, sparse=True), dot(self.E_start[i], Vjx, sparse=True))
            x1 = tf.nn.bias_add(x1, self.bv1[i])
            x1 = tf.nn.sigmoid(x1)
            Uix = dot(x, self.Ui1[i])
            Ujx = dot(x, self.Uj1[i])
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), tf.multiply(x1, x2), sparse=True))
            x = tf.nn.bias_add(x, self.bu1[i])
            x = tf.layers.batch_normalization(x)
            x = tf.nn.relu(x)
            outputs.append(x)
        output = tf.concat(axis=1, values=outputs)

        outputs = []
        for i in range(len(self.E_start)):
            x = output
            # conv2
            Vix = dot(x, self.Vi2[i])
            Vjx = dot(x, self.Vj2[i])
            x1 = tf.add(dot(self.E_end[i], Vix, sparse=True), dot(self.E_start[i], Vjx, sparse=True))
            x1 = tf.nn.bias_add(x1, self.bv1[i])
            x1 = tf.nn.sigmoid(x1)
            Uix = dot(x, self.Ui2[i])
            Ujx = dot(x, self.Uj2[i])
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), tf.multiply(x1, x2), sparse=True))
            x = tf.nn.bias_add(x, self.bu1[i])
            x = tf.layers.batch_normalization(x)
            outputs.append(x)

        output = tf.concat(axis=1, values=outputs)
        output = tf.add(output, tf.matmul(original_x, self.R))
        output = tf.nn.relu(output)

        u = output[:tf.cast(num_users, tf.int32)]
        v = output[tf.cast(num_users, tf.int32):]

        return u, v

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs: # this will if tensors are sparse. sparse_inputs flag needs to be set properly.
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class StackSimple(Layer):
    """ GCN without edge gating """
    def __init__(self, input_dim, output_dim, E_start_list, E_end_list, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(StackSimple, self).__init__(**kwargs)

        assert output_dim % num_support == 0, 'output_dim must be multiple of num_support for stackGC layer'
        assert len(E_start_list) == num_support, 'length of E_start not equal to num_support'

        self.sparse_inputs = sparse_inputs

        with tf.variable_scope(self.name + '_vars'):
            # conv1 (with split weights)
            self.Ui1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Ui1')
            self.Uj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Uj1')
            self.Vi1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vi1')
            self.Vj1 = self.get_weight_variable(input_dim, output_dim, num_support, 'Vj1')
            self.bu1 = self.get_bias_variable(output_dim, num_support, 'bu1')
            self.bv1 = self.get_bias_variable(output_dim, num_support, 'bv1')

            # conv2 (with split weights)
            self.Ui2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Ui2')
            self.Uj2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Uj2')
            self.Vi2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Vi2')
            self.Vj2 = self.get_weight_variable(output_dim, output_dim, num_support, 'Vj2')
            self.bu2 = self.get_bias_variable(output_dim, num_support, 'bu2')
            self.bv2 = self.get_bias_variable(output_dim, num_support, 'bv2')
            
            # resnet
            self.R = weight_variable_random_uniform(input_dim, output_dim, name='R')

        self.dropout = dropout
        self.act = act

        self.E_start = E_start_list
        self.E_end = E_end_list

        if self.logging:
            self._log_vars()

    def get_weight_variable(self, input_dim, output_dim, num_support, name):
        var = weight_variable_random_uniform(input_dim, output_dim, name=name)
        var = tf.split(value=var, axis=1, num_or_size_splits=num_support)
        return var

    def get_bias_variable(self, output_dim, num_support, name):
        var = bias_variable_zero(output_dim, name=name)
        var = tf.split(value=var, axis=0, num_or_size_splits=num_support)
        return var

    def _call(self, inputs):
        if self.sparse_inputs:
            num_users = inputs[0].dense_shape[0]
            num_items = inputs[1].dense_shape[0]
            users = tf.sparse_to_dense(inputs[0].indices, inputs[0].dense_shape, inputs[0].values)
            items = tf.sparse_to_dense(inputs[1].indices, inputs[1].dense_shape, inputs[1].values)
        else:
            num_users = inputs[0].shape[0]
            num_items = inputs[1].shape[0]
            users = inputs[0]
            items = inputs[1]
        
        original_x = tf.concat([users, items], axis=0)  # CHECK THIS! need to combine users and items into one single array. becomes 6000 (users+items) x 6000 (input_dim)
        original_x = tf.nn.dropout(original_x, 1-self.dropout)

        outputs = []
        for i in range(len(self.E_start)):
            # E_start, E_end : E x V
            x = original_x
            # conv1
            Uix = dot(x, self.Ui1[i])
            Ujx = dot(x, self.Uj1[i])
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), x2, sparse=True))
            x = tf.nn.bias_add(x, self.bu1[i])
            x = tf.layers.batch_normalization(x)
            x = tf.nn.relu(x)
            outputs.append(x)
        output = tf.concat(axis=1, values=outputs)

        outputs = []
        for i in range(len(self.E_start)):
            x = output
            # conv2
            Uix = dot(x, self.Ui2[i])
            Ujx = dot(x, self.Uj2[i])
            x2 = dot(self.E_start[i], Ujx, sparse=True)
            x = tf.add(Uix, dot(tf.sparse_transpose(self.E_end[i]), x2, sparse=True))
            x = tf.nn.bias_add(x, self.bu1[i])
            x = tf.layers.batch_normalization(x)
            outputs.append(x)

        output = tf.concat(axis=1, values=outputs)
        output = tf.add(output, tf.matmul(original_x, self.R))
        output = tf.nn.relu(output)

        u = output[:tf.cast(num_users, tf.int32)]
        v = output[tf.cast(num_users, tf.int32):]

        return u, v

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs: # this will if tensors are sparse. sparse_inputs flag needs to be set properly.
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class Simple(Layer):
    """ Fully connected layer to produce graph node embeddings """
    def __init__(self, input_dim, output_dim, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(Simple, self).__init__(**kwargs)
        self.sparse_inputs = sparse_inputs

        with tf.variable_scope(self.name + '_vars'):
            self.W1 = weight_variable_random_uniform(input_dim, output_dim, name='W1')
            self.b1 = bias_variable_zero(output_dim, name='b1')
            self.W2 = weight_variable_random_uniform(output_dim, output_dim, name='W2')
            self.b2 = bias_variable_zero(output_dim, name='b2')

        self.dropout = dropout
        self.act = act

        if self.logging:
            self._log_vars()

    def get_weight_variable(self, input_dim, output_dim, num_support, name):
        var = weight_variable_random_uniform(input_dim, output_dim, name=name)
        var = tf.split(value=var, axis=1, num_or_size_splits=num_support)
        return var

    def get_bias_variable(self, output_dim, num_support, name):
        var = bias_variable_zero(output_dim, name=name)
        var = tf.split(value=var, axis=0, num_or_size_splits=num_support)
        return var

    def _call(self, inputs):
        if self.sparse_inputs:
            num_users = inputs[0].dense_shape[0]
            num_items = inputs[1].dense_shape[0]
            users = tf.sparse_to_dense(inputs[0].indices, inputs[0].dense_shape, inputs[0].values)
            items = tf.sparse_to_dense(inputs[1].indices, inputs[1].dense_shape, inputs[1].values)
        else:
            num_users = inputs[0].shape[0]
            num_items = inputs[1].shape[0]
            users = inputs[0]
            items = inputs[1]
        
        x = tf.concat([users, items], axis=0)  # CHECK THIS! need to combine users and items into one single array. becomes 6000 (users+items) x 6000 (input_dim)
        x = tf.nn.dropout(x, 1-self.dropout)
        x = tf.nn.bias_add(dot(x, self.W1), self.b1)
        x = tf.nn.bias_add(dot(x, self.W2), self.b2)

        u = x[:tf.cast(num_users, tf.int32)]
        v = x[tf.cast(num_users, tf.int32):]

        return u, v

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs: # this will if tensors are sparse. sparse_inputs flag needs to be set properly.
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

""" NEW LAYER """
class StackGCNGate(Layer):
    """Graph convolution layer for bipartite graphs and sparse inputs. (WITH GATE)"""

    def __init__(self, input_dim, output_dim, support, support_t, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(StackGCNGate, self).__init__(**kwargs)

        assert output_dim % num_support == 0, 'output_dim must be multiple of num_support for stackGC layer'

        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights_u'] = weight_variable_random_uniform(input_dim, output_dim, name='weights_u')

            if not share_user_item_weights:
                self.vars['weights_v'] = weight_variable_random_uniform(input_dim, output_dim, name='weights_v')

            else:
                self.vars['weights_v'] = self.vars['weights_u']

        self.weights_u = tf.split(value=self.vars['weights_u'], axis=1, num_or_size_splits=num_support)  # <-- HERE is where weights get split
        self.weights_v = tf.split(value=self.vars['weights_v'], axis=1, num_or_size_splits=num_support)

        self.weights_A = weight_variable_random_uniform(input_dim, output_dim, name='weights_A') # weight to calculate edge gate
        self.weights_B = weight_variable_random_uniform(input_dim, output_dim, name='weights_B') # weight to calculate edge gate
        self.weights_A = tf.split(value=self.weights_A, axis=1, num_or_size_splits=num_support)  # <-- HERE is where weights get split
        self.weights_B = tf.split(value=self.weights_B, axis=1, num_or_size_splits=num_support)

        self.dropout = dropout

        self.sparse_inputs = sparse_inputs
        self.u_features_nonzero = u_features_nonzero
        self.v_features_nonzero = v_features_nonzero
        if sparse_inputs:
            assert u_features_nonzero is not None and v_features_nonzero is not None, \
                'u_features_nonzero and v_features_nonzero can not be None when sparse_inputs is True'

        self.support = tf.sparse_split(axis=1, num_split=num_support, sp_input=support)
        self.support_transpose = tf.sparse_split(axis=1, num_split=num_support, sp_input=support_t)

        self.act = act

        if self.logging:
            self._log_vars()

    def _call(self, inputs):
        x_u = inputs[0]
        x_v = inputs[1]

        if self.sparse_inputs:
            x_u = dropout_sparse(x_u, 1 - self.dropout, self.u_features_nonzero)
            x_v = dropout_sparse(x_v, 1 - self.dropout, self.v_features_nonzero)
        else:
            x_u = tf.nn.dropout(x_u, 1 - self.dropout)
            x_v = tf.nn.dropout(x_v, 1 - self.dropout)

        supports_u = []
        supports_v = []

        for i in range(len(self.support)):
            tmp_u = dot(x_u, self.weights_u[i], sparse=self.sparse_inputs)
            tmp_v = dot(x_v, self.weights_v[i], sparse=self.sparse_inputs)

            A = dot(x_u, self.weights_A[i], sparse=self.sparse_inputs)
            B = dot(x_v, self.weights_B[i], sparse=self.sparse_inputs)
            gate = tf.nn.sigmoid(tf.add(A, B))

            support = self.support[i]
            support_transpose = self.support_transpose[i]

            # print('SUPPORT SHAPE: {}'.format(support.get_shape()))

            tmp_v = tf.multiply(tmp_v, gate)
            tmp_u = tf.multiply(tmp_u, gate)
            mu_u = tf.sparse_tensor_dense_matmul(support, tmp_v)
            mu_v = tf.sparse_tensor_dense_matmul(support_transpose, tmp_u)
            supports_u.append(mu_u)
            supports_v.append(mu_v)
            # supports_u.append(dot(tf.sparse_tensor_to_dense(support), tmp_v, sparse=False))
            # supports_v.append(dot(tf.sparse_tensor_to_dense(support_transpose), tmp_u, sparse=False))

        z_u = tf.concat(axis=1, values=supports_u)
        z_v = tf.concat(axis=1, values=supports_v)

        u_outputs = self.act(z_u)
        v_outputs = self.act(z_v)

        return u_outputs, v_outputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v

class StackGCN(Layer):
    """Graph convolution layer for bipartite graphs and sparse inputs."""

    def __init__(self, input_dim, output_dim, support, support_t, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, share_user_item_weights=True, **kwargs):
        super(StackGCN, self).__init__(**kwargs)

        assert output_dim % num_support == 0, 'output_dim must be multiple of num_support for stackGC layer'

        with tf.variable_scope(self.name + '_vars'):
            self.vars['weights_u'] = weight_variable_random_uniform(input_dim, output_dim, name='weights_u')

            if not share_user_item_weights:
                self.vars['weights_v'] = weight_variable_random_uniform(input_dim, output_dim, name='weights_v')

            else:
                self.vars['weights_v'] = self.vars['weights_u']

        self.weights_u = tf.split(value=self.vars['weights_u'], axis=1, num_or_size_splits=num_support)  # <-- HERE is where weights get split
        self.weights_v = tf.split(value=self.vars['weights_v'], axis=1, num_or_size_splits=num_support)

        self.dropout = dropout

        self.sparse_inputs = sparse_inputs
        self.u_features_nonzero = u_features_nonzero
        self.v_features_nonzero = v_features_nonzero
        if sparse_inputs:
            assert u_features_nonzero is not None and v_features_nonzero is not None, \
                'u_features_nonzero and v_features_nonzero can not be None when sparse_inputs is True'

        self.support = tf.sparse_split(axis=1, num_split=num_support, sp_input=support)
        self.support_transpose = tf.sparse_split(axis=1, num_split=num_support, sp_input=support_t)

        self.act = act

        if self.logging:
            self._log_vars()

    def _call(self, inputs):
        x_u = inputs[0]
        x_v = inputs[1]

        if self.sparse_inputs:
            x_u = dropout_sparse(x_u, 1 - self.dropout, self.u_features_nonzero)
            x_v = dropout_sparse(x_v, 1 - self.dropout, self.v_features_nonzero)
        else:
            x_u = tf.nn.dropout(x_u, 1 - self.dropout)
            x_v = tf.nn.dropout(x_v, 1 - self.dropout)

        supports_u = []
        supports_v = []

        for i in range(len(self.support)):
            tmp_u = dot(x_u, self.weights_u[i], sparse=self.sparse_inputs)
            tmp_v = dot(x_v, self.weights_v[i], sparse=self.sparse_inputs)

            support = self.support[i]
            support_transpose = self.support_transpose[i]

            # print('SUPPORT SHAPE: {}'.format(support.get_shape()))
            supports_u.append(tf.sparse_tensor_dense_matmul(support, tmp_v))
            supports_v.append(tf.sparse_tensor_dense_matmul(support_transpose, tmp_u)) # for second layer it seems tmp_u is only 2999-dim
            # supports_u.append(dot(tf.sparse_tensor_to_dense(support), tmp_v, sparse=False))
            # supports_v.append(dot(tf.sparse_tensor_to_dense(support_transpose), tmp_u, sparse=False))

        z_u = tf.concat(axis=1, values=supports_u)
        z_v = tf.concat(axis=1, values=supports_v)

        u_outputs = self.act(z_u)
        v_outputs = self.act(z_v)

        print('shape of u_outputs: {}'.format(u_outputs.shape))

        return u_outputs, v_outputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v


class OrdinalMixtureGCN(Layer):

    """Graph convolution layer for bipartite graphs and sparse inputs."""

    def __init__(self, input_dim, output_dim, support, support_t, num_support, u_features_nonzero=None,
                 v_features_nonzero=None, sparse_inputs=False, dropout=0.,
                 act=tf.nn.relu, bias=False, share_user_item_weights=False, self_connections=False, **kwargs):
        super(OrdinalMixtureGCN, self).__init__(**kwargs)

        with tf.variable_scope(self.name + '_vars'):

            self.vars['weights_u'] = tf.stack([weight_variable_random_uniform(input_dim, output_dim,
                                                                             name='weights_u_%d' % i)
                                              for i in range(num_support)], axis=0)

            if bias:
                self.vars['bias_u'] = bias_variable_const([output_dim], 0.01, name="bias_u")

            if not share_user_item_weights:
                self.vars['weights_v'] = tf.stack([weight_variable_random_uniform(input_dim, output_dim,
                                                                                 name='weights_v_%d' % i)
                                                  for i in range(num_support)], axis=0)

                if bias:
                    self.vars['bias_v'] = bias_variable_const([output_dim], 0.01, name="bias_v")

            else:
                self.vars['weights_v'] = self.vars['weights_u']
                if bias:
                    self.vars['bias_v'] = self.vars['bias_u']

        self.weights_u = self.vars['weights_u']
        self.weights_v = self.vars['weights_v']

        self.dropout = dropout

        self.sparse_inputs = sparse_inputs
        self.u_features_nonzero = u_features_nonzero
        self.v_features_nonzero = v_features_nonzero
        if sparse_inputs:
            assert u_features_nonzero is not None and v_features_nonzero is not None, \
                'u_features_nonzero and v_features_nonzero can not be None when sparse_inputs is True'

        self.self_connections = self_connections

        self.bias = bias
        support = tf.sparse_split(axis=1, num_split=num_support, sp_input=support)

        support_t = tf.sparse_split(axis=1, num_split=num_support, sp_input=support_t)

        if self_connections:
            self.support = support[:-1]
            self.support_transpose = support_t[:-1]
            self.u_self_connections = support[-1]
            self.v_self_connections = support_t[-1]
            self.weights_u = self.weights_u[:-1]
            self.weights_v = self.weights_v[:-1]
            self.weights_u_self_conn = self.weights_u[-1]
            self.weights_v_self_conn = self.weights_v[-1]

        else:
            self.support = support
            self.support_transpose = support_t
            self.u_self_connections = None
            self.v_self_connections = None
            self.weights_u_self_conn = None
            self.weights_v_self_conn = None

        self.support_nnz = []
        self.support_transpose_nnz = []
        for i in range(len(self.support)):
            nnz = tf.reduce_sum(tf.shape(self.support[i].values))
            self.support_nnz.append(nnz)
            self.support_transpose_nnz.append(nnz)

        self.act = act

        if self.logging:
            self._log_vars()

    def _call(self, inputs):

        if self.sparse_inputs:
            x_u = dropout_sparse(inputs[0], 1 - self.dropout, self.u_features_nonzero)
            x_v = dropout_sparse(inputs[1], 1 - self.dropout, self.v_features_nonzero)
        else:
            x_u = tf.nn.dropout(inputs[0], 1 - self.dropout)
            x_v = tf.nn.dropout(inputs[1], 1 - self.dropout)

        supports_u = []
        supports_v = []

        # self-connections with identity matrix as support
        if self.self_connections:
            uw = dot(x_u, self.weights_u_self_conn, sparse=self.sparse_inputs)
            supports_u.append(tf.sparse_tensor_dense_matmul(self.u_self_connections, uw))

            vw = dot(x_v, self.weights_v_self_conn, sparse=self.sparse_inputs)
            supports_v.append(tf.sparse_tensor_dense_matmul(self.v_self_connections, vw))

        wu = 0.
        wv = 0.
        for i in range(len(self.support)):
            wu += self.weights_u[i]
            wv += self.weights_v[i]

            # multiply feature matrices with weights
            tmp_u = dot(x_u, wu, sparse=self.sparse_inputs)

            tmp_v = dot(x_v, wv, sparse=self.sparse_inputs)

            support = self.support[i]
            support_transpose = self.support_transpose[i]

            # then multiply with rating matrices
            supports_u.append(tf.sparse_tensor_dense_matmul(support, tmp_v))
            supports_v.append(tf.sparse_tensor_dense_matmul(support_transpose, tmp_u))

        z_u = tf.add_n(supports_u)
        z_v = tf.add_n(supports_v)

        if self.bias:
            z_u = tf.nn.bias_add(z_u, self.vars['bias_u'])
            z_v = tf.nn.bias_add(z_v, self.vars['bias_v'])

        u_outputs = self.act(z_u)
        v_outputs = self.act(z_v)

        return u_outputs, v_outputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])
            outputs_u, outputs_v = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs_u', outputs_u)
                tf.summary.histogram(self.name + '/outputs_v', outputs_v)
            return outputs_u, outputs_v


class BilinearMixture(Layer):
    """
    Decoder model layer for link-prediction with ratings
    To use in combination with bipartite layers.
    """

    def __init__(self, num_classes, u_indices, v_indices, input_dim, num_users, num_items, user_item_bias=False,
                 dropout=0., act=tf.nn.softmax, num_weights=3,
                 diagonal=True, **kwargs):
        super(BilinearMixture, self).__init__(**kwargs)
        with tf.variable_scope(self.name + '_vars'):

            for i in range(num_weights):
                if diagonal:
                    #  Diagonal weight matrices for each class stored as vectors
                    self.vars['weights_%d' % i] = weight_variable_random_uniform(1, input_dim, name='weights_%d' % i)

                else:
                    self.vars['weights_%d' % i] = orthogonal([input_dim, input_dim], name='weights_%d' % i)

            self.vars['weights_scalars'] = weight_variable_random_uniform(num_weights, num_classes,
                                                                          name='weights_u_scalars')

            if user_item_bias:
                self.vars['user_bias'] = bias_variable_zero([num_users, num_classes], name='user_bias')
                self.vars['item_bias'] = bias_variable_zero([num_items, num_classes], name='item_bias')

        self.user_item_bias = user_item_bias

        if diagonal:
            self._multiply_inputs_weights = tf.multiply
        else:
            self._multiply_inputs_weights = tf.matmul

        self.num_classes = num_classes
        self.num_weights = num_weights
        self.u_indices = u_indices
        self.v_indices = v_indices

        self.dropout = dropout
        self.act = act  # default is softmax (as written in paper)
        if self.logging:
            self._log_vars()

    def _call(self, inputs):

        u_inputs = tf.nn.dropout(inputs[0], 1 - self.dropout)
        v_inputs = tf.nn.dropout(inputs[1], 1 - self.dropout)

        u_inputs = tf.gather(u_inputs, self.u_indices) # only predicting for these indices
        v_inputs = tf.gather(v_inputs, self.v_indices)
        # u_inputs = tf.gather(u_inputs, [0, 1, 200, 2999]) # this won't work because u_indices only has 2999 elements

        if self.user_item_bias:
            u_bias = tf.gather(self.vars['user_bias'], self.u_indices)
            v_bias = tf.gather(self.vars['item_bias'], self.v_indices)

        basis_outputs = []
        for i in range(self.num_weights):

            u_w = self._multiply_inputs_weights(u_inputs, self.vars['weights_%d' % i])
            x = tf.reduce_sum(tf.multiply(u_w, v_inputs), axis=1)

            basis_outputs.append(x)

        # Store outputs in (Nu x Nv) x num_classes (num_weights?) tensor and apply activation function. (activation function only applied later?)
        basis_outputs = tf.stack(basis_outputs, axis=1)

        outputs = tf.matmul(basis_outputs,  self.vars['weights_scalars'], transpose_b=False)

        if self.user_item_bias:
            outputs += u_bias
            outputs += v_bias

        outputs = self.act(outputs)

        return outputs

    def __call__(self, inputs):
        with tf.name_scope(self.name):
            if self.logging and not self.sparse_inputs:
                tf.summary.histogram(self.name + '/inputs_u', inputs[0])
                tf.summary.histogram(self.name + '/inputs_v', inputs[1])

            outputs = self._call(inputs)
            if self.logging:
                tf.summary.histogram(self.name + '/outputs', outputs)
            return outputs
