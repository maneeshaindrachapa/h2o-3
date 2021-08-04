#!/usr/bin/env python
# -*- encoding: utf-8 -*-
from __future__ import unicode_literals
from collections import OrderedDict as odict
from copy import deepcopy
from functools import partial
import sys

import bindings as bi
from custom import get_customizations_for, reformat_block

PY3 = sys.version_info[0] == 3
str_type = str if PY3 else (str, unicode)
get_customizations_for = partial(get_customizations_for, 'R')


def get_customizations_or_defaults_for(algo, prop, default=None):
    return get_customizations_for(algo, prop, get_customizations_for('defaults', prop, default))

# ----------------------------------------------------------------------------------------------------------------------
#   Generate per-model classes
# ----------------------------------------------------------------------------------------------------------------------


def gen_module(schema, algo, module):
    # print(str(schema))
    rest_api_version = get_customizations_for(algo, 'rest_api_version', 3)

    doc_preamble = get_customizations_for(algo, 'doc.preamble')
    doc_returns = get_customizations_for(algo, 'doc.returns')
    doc_seealso = get_customizations_for(algo, 'doc.seealso')
    doc_references = get_customizations_for(algo, 'doc.references')
    doc_examples = get_customizations_for(algo, 'doc.examples')

    required_params = get_customizations_or_defaults_for(algo, 'extensions.required_params', [])
    extra_params = get_customizations_or_defaults_for(algo, 'extensions.extra_params', [])
    ellipsis_param = get_customizations_for(algo, 'extensions.ellipsis_param')
    model_name = algo_to_modelname(algo)

    update_param_defaults = get_customizations_for('defaults', 'update_param')
    update_param = get_customizations_for(algo, 'update_param')

    yield "# This file is auto-generated by h2o-3/h2o-bindings/bin/gen_R.py"
    yield "# Copyright 2016 H2O.ai;  Apache License Version 2.0 (see LICENSE for details) \n#'"
    yield "# -------------------------- %s -------------------------- #" % model_name
    # start documentation
    if doc_preamble:
        yield "#'"
        yield reformat_block(doc_preamble, prefix="#' ")
    yield "#'"

    # start doc for signature
    required_params = odict([(p[0] if isinstance(p, tuple) else p, p[1] if isinstance(p, tuple) else None)
                            for p in required_params])
    schema_params = odict([(p['name'], p)
                           for p in schema['parameters']])
    extra_params = odict([(p[0] if isinstance(p, tuple) else p, p[1] if isinstance(p, tuple) else None)
                          for p in extra_params])
    all_params = list(required_params.keys()) + list(schema_params.keys()) + list(extra_params.keys())

    def get_schema_params(pname):
        param = deepcopy(schema_params[pname])
        updates = None
        for update_fn in [update_param, update_param_defaults]:
            if callable(update_fn):
                updates = update_fn(pname, param)
            if updates is not None:
                param = updates
                break
        return param if isinstance(param, (list, tuple)) else [param]  # always return array to support deprecated aliases

    tag = "@param"
    pdocs = odict()
    for pname in all_params:
        if pname in pdocs:  # avoid duplicates (esp. if already included in required_params)
            continue
        if pname in schema_params:
            for param in get_schema_params(pname):  # retrieve potential aliases
                pname = param.get('name')
                if pname:
                    pdocs[pname] = get_customizations_or_defaults_for(algo, 'doc.params.'+pname, get_help(param, indent=len(tag)+4))
        else:
            pdocs[pname] = get_customizations_or_defaults_for(algo, 'doc.params.'+pname)
    if ellipsis_param is not None:
        pdocs['...'] = get_customizations_or_defaults_for(algo, 'doc.params._ellipsis_')
    

    for pname, pdoc in pdocs.items():
        if pdoc:
            yield reformat_block("%s %s %s" % (tag, pname, pdoc.lstrip('\n')), indent=len(tag)+1, indent_first=False, prefix="#' ")

    if doc_returns:
        tag = "@return"
        yield reformat_block("%s %s" % (tag, doc_returns.lstrip('\n')), indent=len(tag)+1, indent_first=False, prefix="#' ")
    if doc_seealso:
        tag = "@seealso"
        yield reformat_block("%s %s" % (tag, doc_seealso.lstrip('\n')), indent=len(tag)+1, indent_first=False, prefix="#' ")
    if doc_references:
        tag = "@references"
        yield reformat_block("%s %s" % (tag, doc_references.lstrip('\n')), indent=len(tag)+1, indent_first=False, prefix="#' ")
    if doc_examples:
        yield "#' @examples"
        yield "#' \dontrun{"
        yield reformat_block(doc_examples, prefix="#' ")
        yield "#' }"
    yield "#' @export"

    # start function signature
    sig_pnames = []
    sig_params = []
    for k, v in required_params.items():
        sig_pnames.append(k)
        sig_params.append(k if v is None else '%s = %s' % (k, v))

    for pname in schema_params:
        params = get_schema_params(pname)
        for param in params:
            pname = param.get('name')  # override local var as param can be an alias of pname
            if pname in required_params or not pname:  # skip schema params already added by required_params, and those explicitly removed
                continue
            sig_pnames.append(pname)
            sig_params.append("%s = %s" % (pname, get_customizations_or_defaults_for(algo, 'doc.signatures.' + pname, get_sig_default_value(param))))

    for k, v in extra_params.items():
        sig_pnames.append(k)
        sig_params.append("%s = %s" % (k, v))
    if ellipsis_param is not None:
        sig_params.append("...")

    param_indent = len("h2o.%s <- function(" % module)
    yield reformat_block("h2o.%s <- function(%s)" % (module, ',\n'.join(sig_params)), indent=param_indent, indent_first=False)

    # start function body
    yield "{"
    yield '\n'.join(gen_set_params(algo, sig_pnames, schema_params, required_params, ellipsis_param=ellipsis_param))

    yield ""
    yield "  # Error check and build model"
    verbose = 'verbose' if 'verbose' in extra_params else 'FALSE'
    yield "  model <- .h2o.modelJob('%s', parms, h2oRestApiVersion=%d, verbose=%s)" % (algo, rest_api_version, verbose)
    with_model = get_customizations_for(algo, 'extensions.with_model')
    if with_model:
        yield ""
        yield reformat_block(with_model, indent=2)
    yield "  return(model)"
    yield "}"

    bulk_pnames_skip = ["model_id",
                        "verbose",
                        "destination_key"] # destination_key is only for SVD
    bulk_params = list(zip(*filter(lambda t: not t[0] in bulk_pnames_skip, zip(sig_pnames, sig_params))))
    bulk_pnames = list(bulk_params[0])
    sig_bulk_params = list(bulk_params[1])
    sig_bulk_params.append("segment_columns = NULL")
    sig_bulk_params.append("segment_models_id = NULL")
    sig_bulk_params.append("parallelism = 1")
    if ellipsis_param is not None:
        sig_bulk_params.append("...")

    if algo != "generic":
        #
        # Segment model building
        #
        bulk_param_indent = len(".h2o.train_segments_%s <- function(" % module)
        yield reformat_block(".h2o.train_segments_%s <- function(%s)" % (module, ',\n'.join(sig_bulk_params)), indent=bulk_param_indent, indent_first=False)
    
        # start train_segments-function body
        yield "{"
        yield '\n'.join(gen_set_params(algo, bulk_pnames, schema_params, required_params, skip_params=bulk_pnames_skip, ellipsis_param=ellipsis_param))
        yield ""
        yield "  # Build segment-models specific parameters"
        yield "  segment_parms <- list()"
        yield "  if (!missing(segment_columns))"
        yield "    segment_parms$segment_columns <- segment_columns"
        yield "  if (!missing(segment_models_id))"
        yield "    segment_parms$segment_models_id <- segment_models_id"
        yield "  segment_parms$parallelism <- parallelism"
        yield ""
        yield "  # Error check and build segment models"
        yield "  segment_models <- .h2o.segmentModelsJob('%s', segment_parms, parms, h2oRestApiVersion=%d)" % (algo, rest_api_version)
        yield "  return(segment_models)"
        yield "}"

    #
    # Additional functions
    #
    module_extensions = get_customizations_for(algo, 'extensions.module')
    if module_extensions:
        yield ""
        yield module_extensions


def gen_set_params(algo, pnames, schema_params, required_params, skip_params=None, ellipsis_param=None):
    if ellipsis_param:
        yield reformat_block(ellipsis_param, indent=2)
    if skip_params:
        yield "  # formally define variables that were excluded from function parameters"
        for pname in skip_params:
           yield "  %s <- NULL" % pname
    validate_frames = get_customizations_or_defaults_for(algo, 'extensions.validate_frames')
    if validate_frames:
        yield "  # Validate required training_frame first and other frame args: should be a valid key or an H2OFrame object"
        yield reformat_block(validate_frames, indent=2)
    else:
        frames = get_customizations_or_defaults_for(algo, 'extensions.frame_params', [])
        if frames:
            yield "  # Validate required training_frame first and other frame args: should be a valid key or an H2OFrame object"
        for frame in frames:
            if frame in pnames:
                required_val = str(frame in required_params).upper()
                yield "  {frame} <- .validate.H2OFrame({frame}, required={required})".format(frame=frame, required=required_val)

    validate_required_params = get_customizations_or_defaults_for(algo, 'extensions.validate_required_params')
    if validate_required_params:
        yield ""
        yield "  # Validate other required args"
        yield reformat_block(validate_required_params, indent=2)

    validate_params = get_customizations_or_defaults_for(algo, 'extensions.validate_params')
    if validate_params:
        yield ""
        yield "  # Validate other args"
        yield reformat_block(validate_params, indent=2)

    yield ""
    yield "  # Build parameter list to send to model builder"
    yield "  parms <- list()"
    set_required_params = get_customizations_or_defaults_for(algo, 'extensions.set_required_params')
    if set_required_params:
        yield reformat_block(set_required_params, indent=2)

    skip_default_set_params = get_customizations_or_defaults_for(algo, 'extensions.skip_default_set_params_for', [])
    yield ""
    for pname in schema_params:
        if pname in skip_default_set_params or (skip_params and pname in skip_params):
            continue

        # leave the special handling of 'loss' param here for now as it is used by several algos
        if pname == "loss":
            yield "  if(!missing(loss)) {"
            yield "    if(loss == \"MeanSquare\") {"
            yield "      warning(\"Loss name 'MeanSquare' is deprecated; please use 'Quadratic' instead.\")"
            yield "      parms$loss <- \"Quadratic\""
            yield "    } else "
            yield "      parms$loss <- loss"
            yield "  }"
        else:
            yield "  if (!missing(%s))" % pname
            yield "    parms$%s <- %s" % (pname, pname)

    set_params = get_customizations_or_defaults_for(algo, 'extensions.set_params')
    if set_params:
        yield ""
        yield reformat_block(set_params, indent=2)


def algo_to_modelname(algo):
    if algo == "aggregator": return "H2O Aggregator Model"
    if algo == "deeplearning": return "Deep Learning - Neural Network"
    if algo == "xgboost": return "XGBoost"
    if algo == "drf": return "Random Forest Model in H2O"
    if algo == "gbm": return "Gradient Boosting Machine"
    if algo == "glm": return "H2O Generalized Linear Models"
    if algo == "glrm": return "Generalized Low Rank Model"
    if algo == "kmeans": return "KMeans Model in H2O"
    if algo == "naivebayes": return "Naive Bayes Model in H2O"
    if algo == "pca": return "Principal Components Analysis"
    if algo == "svd": return "Singular Value Decomposition"
    if algo == "stackedensemble": return "H2O Stacked Ensemble"
    if algo == "psvm": return "Support Vector Machine"
    if algo == "anovaglm": return "ANOVA GLM"
    if algo == "targetencoder": return "Target Encoder"
    if algo == "gam": return "Generalized Additive Model"
    if algo == "maxrglm": return "Maximum R GLM"
    return algo


def get_help(param, indent=0):
    pname = param.get('name')
    ptype = param.get('type')
    pvalues = param.get('values')
    pdefault = param.get('default_value')
    phelp = param.get('help')
    if not phelp:
        return
    if ptype == 'boolean':
        phelp = "\code{Logical}. " + phelp
    if pvalues:
        phelp += " Must be one of: %s." % ", ".join('"%s"' % v for v in pvalues)
    if pdefault is not None:
        phelp += " Defaults to %s." % get_doc_default_value(param)
    return bi.wrap(phelp, width=120-indent)


def get_doc_default_value(param):
    ptype = param['type']
    ptype = 'str' if ptype.startswith('enum') else ptype  # for doc, default value is actually a str for enum types.
    return as_R_repr(ptype, param.get('default_value'))


def get_sig_default_value(param):
    ptype = param['type']
    value = (param.get('values') if ptype.startswith('enum')  # for signature, default value is whole enum (to provide parameter hint).
             else param.get('default_value'))
    return as_R_repr(ptype, value)


def as_R_repr(ptype, value):
    if value is None:
        return (0 if ptype in ['short', 'int', 'long', 'double']
                else "list()" if ptype == 'list'
                else 'NULL')
    if ptype == 'boolean':
        return str(value).upper()
    if ptype == 'double':
        return '%.10g' % value
    if ptype == 'list':
        return "list(%s)" % ', '.join('"%s"' % v for v in value)
    if ptype.startswith('enum'):
        return "c(%s)" % ', '.join('"%s"' % v for v in value)
    if ptype.endswith('[]'):
        return "c(%s)" % ', '.join('%s' % v for v in value)
    return value


# ----------------------------------------------------------------------------------------------------------------------
#   MAIN:
# ----------------------------------------------------------------------------------------------------------------------
def main():
    bi.init("R", "../../../h2o-r/h2o-package/R", clear_dir=False)

    for name, mb in bi.model_builders().items():
        module = name
        file_name = name
        if name == "drf":
            module = "randomForest"
            file_name = "randomforest"
        if name == "isolationforest": module = "isolationForest"
        if name == "extendedisolationforest": module = "extendedIsolationForest"
        if name == "naivebayes": module = "naiveBayes"
        if name == "stackedensemble": module = "stackedEnsemble"
        if name == "pca": module = "prcomp"
        bi.vprint("Generating model: " + name)
        bi.write_to_file("%s.R" % file_name, gen_module(mb, name, module))

if __name__ == "__main__":
    main()
