import os
from bipy.utils import (replace_suffix, which, remove_suffix, append_stem,
                        prepare_ref_file)
import sh
from bcbio.utils import file_exists, safe_makedir, add_full_path
import glob
import pandas as pd
from math import sqrt
from mako.template import Template
from bipy.toolbox.reporting import LatexReport, safe_latex
from bcbio.distributed.transaction import file_transaction
from bipy.pipeline.stages import AbstractStage
from bcbio.broad import BroadRunner, picardrun
from bcbio.log import logger
from bcbio.provenance import do


def program_exists(path):
    return which(path)


def _results_dir(config, prefix=""):

    def _make_dir(base):
        out_dir = os.path.join(base, "rseqc",
                               prefix)
        safe_makedir(out_dir)
        return out_dir

    if "dir" not in config:
        return _make_dir("results")
    if "results" not in config["dir"]:
        return _make_dir("results")
    else:
        return _make_dir(config["dir"]["results"])


def _fetch_chrom_sizes(config):

    PROGRAM = "fetchChromSizes"

    if not program_exists(PROGRAM):
        logger.error("%s is not in the path or is not executable. Make sure "
                     "it is installed or go to "
                     "http://hgdownload.cse.ucsc.edu/admin/exe/"
                     "to download it." % (PROGRAM))
        exit(1)

    if "annotation" not in config:
        logger.error("'annotation' must be in the yaml file. See example "
                     " configuration files")
        exit(1)
    if "name" not in config["annotation"]:
        logger.error("'name' must be in the yaml file under  "
                     " 'annotation'. See example configuration files.")
        exit(1)
    genome = config["annotation"]["name"]
    chrom_size_file = os.path.join(_results_dir(config),
                                   genome + ".sizes")
    if file_exists(chrom_size_file):
        return chrom_size_file

    with file_transaction(chrom_size_file) as tmp_chrom_size_file:
        sh.fetchChromSizes(genome, _out=tmp_chrom_size_file)

    if not file_exists(chrom_size_file):
        logger.error("chromosome size file does not exist. Check "
                     "'annotation': 'name' to make sure it is valid.")
        exit(1)
    return chrom_size_file


def bam2bigwig(in_file, config, out_prefix=None):
    """
    assumes the library preparation was not strand specific for now
    """
    PROGRAM = "bam2wig.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "bigwig"
    chrom_size_file = config["annotation"].get("chrom_size_file", None)
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    if not chrom_size_file:
        chrom_size_file = _fetch_chrom_sizes(config)
    wiggle_file = out_prefix + ".wig"

    if not file_exists(wiggle_file):
        bam2wig = sh.Command(which(PROGRAM))
        cmd = str(bam2wig.bake(i=in_file, s=chrom_size_file, o=out_prefix))
        do.run(cmd, "Converting %s from BAM to bigwig" % (in_file), None)

    bigwig_file = out_prefix + ".bw"

    return wig2bigwig(wiggle_file, chrom_size_file, bigwig_file)


def wig2bigwig(wiggle_file, chrom_size_file, out_file):
    """
    convert wiggle file to bigwig file using the UCSC tool
    """
    PROGRAM = "wigToBigWig"
    if not program_exists(PROGRAM):
        logger.error("%s is not in the path or is not executable. Make sure "
                     "it is installed or go to "
                     "http://hgdownload.cse.ucsc.edu/admin/exe/"
                     "to download it." % (PROGRAM))
        exit(1)

    if file_exists(out_file):
        return out_file

    wigToBigWig = sh.Command(which(PROGRAM))
    with file_transaction(out_file) as tx_out_file:
        cmd = str(wigToBigWig.bake(wiggle_file, chrom_size_file, tx_out_file))
        do.run(cmd, "Converting %s from wig to bigwig." % (wiggle_file), None)
    return out_file


def bam_stat(in_file, config, out_prefix=None):
    """
    dump read maping statistics from a SAM or BAM file to out_file
    """
    PROGRAM = "bam_stat.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "bam_stat"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    out_file = out_prefix + ".txt"
    if file_exists(out_file):
        return out_file

    bam_stat_run = sh.Command(which(PROGRAM))
    with file_transaction(out_file) as tx_out_file:
        cmd = str(bam_stat_run.bake(i=in_file, _err=tx_out_file))
        do.run(cmd, "Calculating BAM statistics from %s." % (in_file), None)

    return out_file


def clipping_profile(in_file, config, out_prefix=None):
    """
    estimate the clipping profile of the reads
    """
    PROGRAM = "clipping_profile.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "clipping"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, "clipping")
    clip_plot_file = out_prefix + ".clipping_profile.pdf"
    print clip_plot_file
    if file_exists(clip_plot_file):
        return clip_plot_file

    clip_run = sh.Command(which(PROGRAM))
    cmd = str(clip_run.bake(i=in_file, o=out_prefix))
    do.run(cmd, "Calculating 5' and 3' clipping profile of %s." % (in_file), None)

    return clip_plot_file


def genebody_coverage(in_file, config, out_prefix=None):
    """
    used to check the 5'/3' bias across transcripts
    """
    PROGRAM = "geneBody_coverage.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "coverage"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    coverage_plot_file = out_prefix + ".geneBodyCoverage.pdf"
    if file_exists(coverage_plot_file):
        return coverage_plot_file

    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)
    coverage_run = sh.Command(which(PROGRAM))
    cmd = str(coverage_run.bake(i=in_file, r=bed, o=out_prefix))
    do.run(cmd, "Calculating coverage of %s." % (in_file), None)
    return coverage_plot_file

def genebody_coverage2(in_file, config, out_prefix=None):
    """
    used to check the 5'/3' bias across transcripts, takes a bam file,
    converts it to bigwig and then uses that
    """
    PROGRAM = "geneBody_coverage2.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    in_bigwig = bam2bigwig(in_file, config)
    prefix = "coverage"
    out_dir = os.path.join(os.path.dirname(in_bigwig), os.pardir, "coverage")
    safe_makedir(out_dir)
    out_prefix = out_dir + "/wiggle"
    #out_prefix = _get_out_prefix(in_bigwig, config, out_prefix, prefix)
    coverage_plot_file = out_prefix + ".geneBodyCoverage.pdf"
    if file_exists(coverage_plot_file):
        return coverage_plot_file

    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)
    coverage_run = sh.Command(which(PROGRAM))
    cmd = str(coverage_run.bake(i=in_bigwig, r=bed, o=out_prefix, t="pdf"))
    do.run(cmd, "Calculating coverage of %s." % (in_bigwig), None)
    return coverage_plot_file

def junction_annotation(in_file, config, out_prefix=None):
    """
    compile novel/known information about splice junctions
    """
    PROGRAM = "junction_annotation.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "junction"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    junction_file = out_prefix + ".splice_junction.pdf"
    if file_exists(junction_file):
        return junction_file
    junction_run = sh.Command(which(PROGRAM))
    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)
    cmd = str(junction_run.bake(i=in_file, o=out_prefix, r=bed))
    do.run(cmd, "Calculating novel/known information about splice junctions of "
           "%s." % (in_file), None)
    return junction_file


def junction_saturation(in_file, config, out_prefix=None):
    """
    check if splicing is deep enough to perform alternative splicing
    analysis
    """
    PROGRAM = "junction_saturation.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "saturation"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    saturation_file = out_prefix + ".junctionSaturation_plot.pdf"
    if file_exists(saturation_file):
        return saturation_file

    saturation_run = sh.Command(which(PROGRAM))
    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)
    cmd = str(saturation_run.bake(i=in_file, o=out_prefix, r=bed))
    do.run(cmd, "Calculating junction saturation estimation of %s." % in_file,
           None)
    return saturation_file


def RPKM_count(in_file, config, out_prefix=None):
    """
    produce RPKM
    """
    PROGRAM = "RPKM_count.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "RPKM_count"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    rpkm_count_file = out_prefix + "_read_count.xls"
    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)
    if file_exists(rpkm_count_file):
        return rpkm_count_file
    RPKM_count_run = sh.Command(which(PROGRAM))
    cmd = str(RPKM_count_run.bake(i=in_file, r=bed, o=out_prefix))
    do.run(cmd, "Calculating RPKM of %s using reference %s." % (in_file, bed),
           None)
    return rpkm_count_file


def merge_RPKM(in_dir):
    """
    reads in all RPKM_count files in a directory and combines them into
    one file
    """
    HEADER = ["chrom", "st", "end", "accession" "score",
              "gene_strand", "tag_count", "RPKM"]
    NAME_COL = 3
    HEADER_START = 0
    HEADER_END = 5
    RPKM_COLUMN = 6
    COUNT_COLUMN = 5

    merged_file = os.path.join(in_dir, "RPKM.combined.txt")
    if file_exists(merged_file):
        return merged_file

    RPKM_files = glob.glob(os.path.join(in_dir, "*_read_count.xls"))
    col_names = map(os.path.basename, map(remove_suffix, RPKM_files))
    dataframes = [pd.DataFrame.from_csv(f, sep="\t", index_col=NAME_COL)
                  for f in RPKM_files]
    # make sure the data frames are sorted in the same order
    dataframes = [d.sort_index() for d in dataframes]
    # get the header rows (columns 0-6)
    common = dataframes[0].ix[:, HEADER_START:HEADER_END]
    items = zip(col_names, dataframes)
    for item in items:
        common[item[0] + "_count"] = item[1].ix[:, COUNT_COLUMN]
        common[item[0] + "_RPKM"] = item[1].ix[:, RPKM_COLUMN]

    count_columns = [item[0] + "_count" for item in items]
    rpkm_columns = [item[0] + "_RPKM" for item in items]

    common["count_total"] = common.ix[:, count_columns].sum(axis=1)
    common["RPKM_mean"] = common.ix[:, rpkm_columns].mean(axis=1)
    if len(rpkm_columns) > 1:
        common["RPKM_sd"] = common.ix[:, rpkm_columns].std(axis=1)
        common["RPKM_sem"] = common["RPKM_sd"] / sqrt(len(rpkm_columns))
    else:
        common["RPKM_sd"] = float('NaN')
        common["RPKM_sem"] = float('NaN')

    common.to_csv(merged_file, sep="\t")
    return merged_file


def fix_RPKM_count_file(in_file, out_file=None):
    """
    splits the RPKM_count file id column into two separate columns;
    one with the id and the other with the feature
    """

    if not out_file:
        out_file = append_stem(in_file, "fixed")

    if file_exists(out_file):
        return out_file

    with open(in_file) as in_handle:
        rpkm = pd.io.parsers.read_table(in_handle)
        rpkm["gene_id"] = rpkm["accession"].apply(lambda x:
                                                  x.rsplit("_", 2)[0])
        rpkm["feature"] = rpkm["accession"].apply(lambda x:
                                                  x.rsplit("_", 2)[1])
        # remove the '#' character since it denotes a comment
        rpkm = rpkm.rename(columns={"#chrom": "chrom"})

    with file_transaction(out_file) as tmp_out_file:
        rpkm.to_csv(tmp_out_file, sep="\t", index=False)

    return out_file


def RPKM_saturation(in_file, config, out_prefix=None):
    """
    estimate the precision of RPKM calculation by resampling
    """
    PROGRAM = "RPKM_saturation.py"
    if not program_exists(PROGRAM):
        logger.info("%s is not in the path or is not executable." % (PROGRAM))
        exit(1)

    prefix = "RPKM_saturation"
    out_prefix = _get_out_prefix(in_file, config, out_prefix, prefix)
    rpkm_saturation_file = out_prefix + ".saturation.pdf"
    gtf = _get_gtf(config)
    bed = _gtf2bed(gtf)

    if file_exists(rpkm_saturation_file):
        return rpkm_saturation_file

    RPKM_saturation_run = sh.Command(which(PROGRAM))
    cmd = str(RPKM_saturation_run.bake(i=in_file, r=bed, o=out_prefix))
    do.run(cmd, "Calculating RPKM saturation of %s." % in_file, None)
    return rpkm_saturation_file

def _get_out_dir(in_file, config, out_prefix, prefix):
    if not out_prefix:
        out_dir = os.path.join(_results_dir(config),
                               os.path.basename(in_file),
                               prefix)
        safe_makedir(out_dir)
    return out_dir


def _get_out_prefix(in_file, config, out_prefix, prefix):
    if not out_prefix:
        out_dir = os.path.join(_results_dir(config),
                               os.path.basename(in_file),
                               prefix)
        safe_makedir(out_dir)
        out_prefix = os.path.join(out_dir, prefix)

    return out_prefix


def _get_gtf(config):
    gtf = config["annotation"].get("file", None)
    #gtf = config.get("gtf", None)
    if not gtf or not file_exists(gtf):
        logger.error("genebody_coverage needs a GTF file passed to it.")
        exit(1)
    return gtf


def _gtf2bed(gtf):
    bed = replace_suffix(gtf, "bed")
    if not file_exists(bed):
        sh.gtf2bigbed(gtf, _out=bed)
    return bed


class RseqcParser(object):
    """
    parse a directory full of rseqc results

    """
    DIRS = ["bam_sta", "clipping", "coverage", "junction",
            "saturation", "RPKM_count", "RPKM_saturation"]

    GRAPHS = ((os.path.join("RPKM_saturation", "RPKM_saturation.pdf"),
               "", 1.0),
              (os.path.join("clipping", "clipping.pdf"), "", 1.0),
              (os.path.join("coverage", "coverage.geneBodyCoverage.pdf"),
               "", 1.0),
              (os.path.join("saturation", "junction_saturation.pdf"),
               "", 1.0),
              (os.path.join("junction", "junction.splice_junction.pdf"),
               "", 1.0))

    INFO = (os.path.join("bam_stat", "bam_stat.txt"),
            os.path.join("RPKM_count", "RPKM_count.txt"))

    def __init__(self, base_dir):
        self._dir = base_dir

    def get_rseqc_graphs(self):
        final_graphs = []
        for f, caption, size in self.GRAPHS:
            final_f = add_full_path(os.path.join(self._dir, f))
            if file_exists(final_f):
                final_graphs.append((final_f, caption, size))
        return final_graphs


class RseqcReport(LatexReport):

    CAPTIONS = {"RPKM_saturation.pdf": "",
                "coverage.geneBodyCoverage.pdf": "",
                "clipping.pdf": "",
                "coverage.pdf": "",
                "junction_saturation.pdf": "",
                "splice_events.pdf": ""}

    def __init__(self):
        pass

    def template(self):
        return self._template

    def _add_captions(self, figures):
        new_figures = []
        for figure in figures:
            filename = os.path.basename(figure[0])
            caption = self.CAPTIONS.get(filename, "")
            new_figures.append((figure[0], caption, figure[2]))
        return new_figures

    def clean_figures(self, figures):
        new_figures = []
        for figure in figures:
            filename = safe_latex(figure[0])
            new_figures.append((filename, figure[1], figure[2]))
        return new_figures



    def generate_report(self, name, figures=None):
        template = Template(self._template)
        clean_name = safe_latex(name)
        #clean_figures = self.clean_figures(figures)
        #section = template.render(name=clean_name, figures=clean_figures)
        clean_figures = [(remove_suffix(figure[0]), figure[1], figure[2])
                         for figure in figures]
        section = template.render(name=clean_name, figures=clean_figures)
        return section

    _template = r"""
\subsection*{Rseqc report for ${name}}

% if figures:
    % for i, (figure, caption, size) in enumerate (figures):
    \begin{figure}[htbp]
        \centering
        \includegraphics[width=${size}\linewidth,natwidth=610,natheight=64]{${figure}}
        \caption{${caption}}
    \end{figure}
    % endfor
% endif
"""


class RNASeqMetrics(AbstractStage):
    """
    Runs the Picard tool RNASeqMetrics.
    http://picard.sourceforge.net/picard-metric-definitions.shtml#RnaSeqMetrics

    YAML configuration file must have the following fields:
    program:
        picard: /path/to/picard/jars
    rnaseq_metrics:
        ref:
            name: name_of_organism
            file: /path/to/refflat_file
        ribo: /path/to/rrna_interval_list_file

    Interval list file format:
    http://picard.sourceforge.net/javadoc/net/sf/picard/util/IntervalList.html
    """

    stage = "rnaseq_metrics"

    def __init__(self, config):
        self.config = config
        self.stage_config = config["stage"][self.stage]
        self.ribo = self.stage_config["ribo"]
        self.picard = BroadRunner(config["program"]["picard"], None, {"algorithm": {}})
        self.ref = prepare_ref_file(self.stage_config["ref"],
                                    self.config)

    def out_file(self, in_file):
        results_dir = self.config["dir"].get("results", "results")
        out_dir = os.path.join(results_dir, self.stage)
        safe_makedir(out_dir)
        out_file = replace_suffix(os.path.basename(in_file), "metrics")
        return os.path.join(out_dir, out_file)

    def __call__(self, in_file):
        out_file = self.out_file(in_file)
        if file_exists(out_file):
            return out_file
        out_file = picardrun.picard_rnaseq_metrics(self.picard, in_file,
                                                   self.ref, self.ribo,
                                                   out_file)
        return out_file
