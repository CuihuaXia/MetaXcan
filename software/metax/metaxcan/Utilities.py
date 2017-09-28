import logging
import pandas
import os
import numpy
from scipy import stats

from .. import Constants
from .. import Utilities
from .. import MatrixManager
from ..PredictionModel import WDBQF, WDBEQF, load_model, dataframe_from_weight_data

import AssociationCalculation

class SimpleContext(AssociationCalculation.Context):
    def __init__(self, gwas, model, covariance):
        self.gwas = gwas
        self.model = model
        self.covariance = covariance

    def get_weights(self, gene):
        w = self.model.weights
        w = w[w.gene == gene]
        return w

    def get_covariance(self, gene, snps):
        return self.covariance.get(gene, snps, strict=False)

    def get_n_in_covariance(self, gene):
        return self.covariance.n_snps(gene)

    def get_gwas(self, snps):
        g = self.gwas
        g = g[g[Constants.SNP].isin(snps)]
        return g

    def get_model_snps(self):
        return set(self.model.weights.rsid)

    def get_data_intersection(self):
        return _data_intersection(self.model, self.gwas)

    def provide_calculation(self, gene):
        w = self.get_weights(gene)
        gwas = self.get_gwas(w[WDBQF.K_RSID].values)

        i = pandas.merge(w, gwas, left_on="rsid", right_on="snp")
        if not Constants.BETA in i: i[Constants.BETA] = None
        i = i[[Constants.SNP, WDBQF.K_WEIGHT, Constants.ZSCORE, Constants.BETA]]

        snps, cov = self.get_covariance(gene, i[Constants.SNP].values)

        # fast subsetting and aligning
        d_columns = i.columns.values
        if snps is not None and len(snps):
            d = {x[0]: x for x in i.values}
            d = [d[snp] for snp in snps]
            d = zip(*d)
            d = {d_columns[i]:d[i] for i in xrange(0, len(d_columns))}
            i = pandas.DataFrame(d)
        else:
            i = pandas.DataFrame(columns=d_columns)
        return len(w.weight), i, cov, snps

    def get_model_info(self):
        return self.model.extra

class OptimizedContext(SimpleContext):
    def __init__(self, gwas, model, covariance):
        self.covariance = covariance
        self.weight_data, self.snps_in_model = _prepare_weight_data(model)
        self.gwas_data = _prepare_gwas_data(gwas)
        self.extra = model.extra

    def _get_weights(self, gene):
        w = self.weight_data[gene]
        w = {x[WDBQF.RSID]:x[WDBQF.WEIGHT] for x in w}
        return w

    def get_weights(self, gene):
        w = self.weight_data[gene]
        w = dataframe_from_weight_data(zip(*w))
        return w

    def get_model_snps(self):
        return set(self.snps_in_model)

    def _get_gwas(self, snps):
        snps = set(snps)
        g = self.gwas_data
        g = [g[x] for x in snps if x in g]
        g = {x[0]:(x[1], x[2]) for x in g}
        return g

    def get_gwas(self, snps):
        snps = set(snps)
        g = self.gwas_data
        g = [g[x] for x in snps if x in g]
        if len(g):
            g = zip(*g)
            g = pandas.DataFrame({Constants.SNP:g[0], Constants.ZSCORE:g[1], Constants.BETA:g[2]})
        else:
            g = pandas.DataFrame(columns=[Constants.SNP, Constants.ZSCORE, Constants.BETA])
        return g

    def get_data_intersection(self):
        return _data_intersection_2(self.weight_data, self.gwas_data)

    def provide_calculation(self, gene):
        w = self._get_weights(gene)
        gwas = self._get_gwas(w.keys())
        type = [numpy.str, numpy.float64, numpy.float64, numpy.float64]
        columns = [Constants.SNP, WDBQF.K_WEIGHT, Constants.ZSCORE, Constants.BETA]
        d = {x: v for x, v in w.iteritems() if x in gwas}

        snps, cov = self.get_covariance(gene, d.keys())
        if snps is None:
            d = pandas.DataFrame(columns=columns)
            return len(w), d, cov, snps

        d = [(x, w[x], gwas[x][0], gwas[x][1]) for x in snps]
        d = zip(*d)
        if len(d):
            d = {columns[i]:numpy.array(d[i], dtype=type[i]) for i in xrange(0,len(columns))}
        else:
            d = {columns[i]:numpy.array([]) for i in xrange(0,len(columns))}

        return  len(w), d, cov, snps

    def get_model_info(self):
        return self.extra


def _data_intersection(model, gwas):
    weights = model.weights
    k = pandas.merge(weights, gwas, how='inner', left_on="rsid", right_on="snp")
    genes = k.gene.drop_duplicates().values
    snps = k.rsid.drop_duplicates().values
    return genes, snps

def _data_intersection_2(weight_data, gwas_data):
    genes = set()
    snps = set()
    for gene, entries in weight_data.iteritems():
        gs = zip(*entries)[WDBQF.RSID]
        for s in gs:
            if s in gwas_data:
                genes.add(gene)
                snps.add(s)
    return genes, snps

def _sanitized_gwas(gwas):
    gwas = gwas[[Constants.SNP, Constants.ZSCORE, Constants.BETA]]
    if numpy.any(~ numpy.isfinite(gwas[Constants.ZSCORE])):
        logging.warning("Discarding non finite GWAS zscores")
        gwas = gwas.loc[numpy.isfinite(gwas[Constants.ZSCORE])]
    return gwas

def _prepare_gwas(gwas):
    #If zscore is numeric, then everything is fine with us.
    # if not, try to remove "NA" strings.
    try:
        i = gwas.zscore != "NA"
        gwas = gwas.loc[i]
        gwas = pandas.DataFrame(gwas)
        gwas.loc[:,Constants.ZSCORE] = gwas.zscore.astype(numpy.float64)
    except Exception as e:
        logging.info("Unexpected issue preparing gwas... %s", str(e))
        pass

    if not Constants.BETA in gwas:
        gwas.loc[:,Constants.BETA] = numpy.nan

    return gwas

def _prepare_gwas_data(gwas):
    data = {}
    for x in gwas.values:
        data[x[0]] = x
    return data

def _prepare_model(model):
    K = WDBQF.K_GENE
    g = model.weights[K]
    model.weights[K] = pandas.Categorical(g, g.drop_duplicates())
    return model

def _prepare_weight_data(model):
    d = {}
    snps = set()
    for x in model.weights.values:
        gene = x[WDBQF.GENE]
        if not gene in d:
            d[gene] = []
        entries = d[gene]
        entries.append(x)
        snps.add(x[WDBQF.RSID])
    return d, snps

def _beta_loader(args):
    beta_contents = Utilities.contentsWithPatternsFromFolder(args.beta_folder, [])
    r = pandas.DataFrame()
    for beta_name in beta_contents:
        logging.info("Processing %s", beta_name)
        beta_path = os.path.join(args.beta_folder, beta_name)
        b = pandas.read_table(beta_path)
        r = pandas.concat([r, b])
    return r

def _gwas_wrapper(gwas):
    logging.info("Processing input gwas")
    return gwas

def build_context(args, gwas):
    logging.info("Loading model from: %s", args.model_db_path)
    model = load_model(args.model_db_path)

    logging.info("Loading covariance data from: %s", args.covariance)
    covariance_manager = MatrixManager.load_matrix_manager(args.covariance)

    gwas = _gwas_wrapper(gwas) if gwas is not None else _beta_loader(args)
    context = _build_context(model, covariance_manager, gwas)
    return context

def _build_context(model, covariance_manager, gwas):
    gwas = _prepare_gwas(gwas)
    gwas = _sanitized_gwas(gwas)
    context = OptimizedContext(gwas, model, covariance_manager)
    return context

def _build_simple_context(model, covariance_manager, gwas):
    model = _prepare_model(model)
    gwas = _prepare_gwas(gwas)
    gwas = _sanitized_gwas(gwas)
    context = SimpleContext(gwas, model, covariance_manager)
    return context

def _to_int(d):
    r = d
    try:
        r = int(d)
    except:
        pass
    return r

def format_output(results, context, remove_ens_version):
    results = results.drop("n_snps_in_model",1)

    # Dodge the use of cdf on non finite values
    i = numpy.isfinite(results.zscore)
    results[Constants.PVALUE] = numpy.nan
    results.loc[i, Constants.PVALUE] = 2 * stats.norm.sf(numpy.abs(results.loc[i, Constants.ZSCORE].values))

    model_info = pandas.DataFrame(context.get_model_info())

    merged = pandas.merge(results, model_info, how="inner", on="gene")
    if remove_ens_version:
        merged.gene = merged.gene.str.split(".").str.get(0)

    K = Constants
    AK = AssociationCalculation.ARF
    column_order = [WDBQF.K_GENE,
                    WDBEQF.K_GENE_NAME,
                    K.ZSCORE,
                    AK.K_EFFECT_SIZE,
                    Constants.PVALUE,
                    AK.K_VAR_G,
                    WDBEQF.K_PRED_PERF_R2,
                    WDBEQF.K_PRED_PERF_PVAL,
                    WDBEQF.K_PRED_PERF_QVAL,
                    AK.K_N_SNPS_USED,
                    AK.K_N_SNPS_IN_COV,
                    WDBEQF.K_N_SNP_IN_MODEL]

    merged = merged[column_order]
    merged = merged.fillna("NA")
    # since we allow NA in covs, we massage it a little bit into resemblying an int instead of a float
    # (pandas uses the NaN float.)
    merged.n_snps_in_cov = merged.n_snps_in_cov.apply(_to_int)
    merged = merged.sort_values(by=Constants.PVALUE)

    return merged
