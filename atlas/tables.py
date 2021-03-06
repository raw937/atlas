import logging
import json
import pandas as pd
import sys
from atlas import TAX_LEVELS


MERGED_HEADER = ["contig", "orf", "taxonomy", "erfc", "orf_taxonomy",
                 "refseq_product", "refseq_evalue", "refseq_bitscore",
                 "uniprot_ac", "eggnog_ssid_b", "eggnog_species_id",
                 "uniprot_id", "ko_id", "ko_level1_name", "ko_level2_name",
                 "ko_level3_id", "ko_level3_name", "ko_gene_symbol",
                 "ko_product", "ko_ec", "eggnog_evalue", "eggnog_bitscore",
                 "enzyme_ec", "enzyme_name", "cazy_gene", "cazy_family",
                 "cazy_class", "cazy_ec", "cog_protein_id", "cog_id",
                 "cog_functional_class", "cog_annotation",
                 "cog_functional_class_description"]
# minus the sam/bam file name which is the last column
COUNTS_HEADER = ["Geneid", "Chr", "Start", "End", "Strand", "Length"]

def merge_tables(tables, output):
    """Takes the output from parsers and combines them into a single TSV table.

    Headers are required and should contain 'contig' and 'orf' column labels.
    """
    index_cols = ["contig", "orf"]

    for i, table in enumerate(tables):
        if i == 0:
            try:
                ref_df = pd.read_table(table, index_col=index_cols)
                logging.info("%d lines read in %s" % (len(ref_df), table.name))
            except ValueError:
                logging.critical("The expected headers ('contig', 'orf') are missing from %s" % table.name)
                sys.exit(1)
        else:
            try:
                tmp_df = pd.read_table(table, index_col=index_cols)
                logging.info("%d lines read in %s" % (len(tmp_df), table.name))
            except ValueError:
                logging.critical("The expected headers ('contig', 'orf') are missing from %s" % table.name)
                sys.exit(1)
            merged = pd.merge(left=ref_df, right=tmp_df, how="outer", left_index=True, right_index=True)
            ref_df = merged.copy()
    logging.info("%d total lines after merging all tables" % len(ref_df))
    ref_df.to_csv(output, sep="\t", na_rep="NA")


def col_split(df, cols, sep='|'):
    """
    Split the values of columns and expand so the new DataFrame has one split value per row.

    Args:
        df (pandas.DataFrame)
        cols (list): column header values
        sep (str): value with which to split

    Returns:
        pandas.DataFrame

    """
    if not cols or len(df) == 0:
        return df

    col_series = []
    for c in cols:
        df[c] = df[c].astype(str)
        col_series.append(df[c].str.split(sep, expand=True).stack().str.strip().reset_index(level=1, drop=True))
    temp_df = pd.concat(col_series, axis=1, keys=cols)
    return df.drop(cols, axis=1).join(temp_df).reset_index(drop=True)


def get_split_cols(df, values):
    if not "contig" in values and "orf" not in values:
        # one-to-many relationships with mapping sequence to values
        if "enzyme_name" in values or "enzyme_ec" in values:
            # these cols have to split in pairs
            cols = []
            if "enzyme_name" in values:
                cols.append("enzyme_name")
            if "enzyme_ec" in values:
                cols.append("enzyme_ec")
            df = col_split(df, cols)
        if "cazy_ec" in values:
            df = col_split(df, ["cazy_ec"])
    return df


def count_tables(prefix, merged, counts, combinations, suffix=".tsv"):
    """Aggregate and integrate count data from `counts` with annotation data in `merged`. The
    merged data is the result of `merge-tables`. Count data is a TSV formatted with a header:

        \b
        Geneid Chr Start  End Strand Length /path/example.bam
        orf1     1     1  500      +    500                50
        orf2     1   601  900      +    300               300
        orf3     1  1201 1500      +    300               200

    `combinations` are specified as a JSON string with key to values pairs, e.g.:

        \b
        '{"KO":["ko_id", "ko_gene_symbol", "ko_product", "ko_ec"], "KO_Product":["ko_product"]}'

    Counts get aggregated (summed) across all values, such that the above example gives two files:

        \b
        <prefix>_KO.tsv

            \b
            ko_id   ko_gene_symbol  ko_product                         ko_ec      count
            K00784  rnz             ribonuclease Z                     3.1.26.11  72
            K01006  ppdK            pyruvate, orthophosphate dikinase  2.7.9.1    177
            K01187  malZ            alpha-glucosidase                  3.2.1.20   91

        \b
        <prefix>_KO_product.tsv

            \b
            ko_product                  count
            alpha-glucosidase           91
            beta-galactosidase          267
            cell division protein FtsQ  8
    """

    def _get_valid_dataframe(file_path, expected_cols, **kwargs):
        """
        Args:
            file_path (str): file path to data
            expected_cols (list): column headers necessary for validation

        Returns:
            pandas.core.frame.DataFrame

        Raises:
            ValueError: lists missing required columns
        """
        df = pd.read_csv(file_path, **kwargs)
        missing_cols = []
        for c in expected_cols:
            if c not in df.columns:
                missing_cols.append(c)
        if len(missing_cols) > 0:
            raise ValueError("%s missing required columns: %s" % (file_path, ", ".join(missing_cols)))
        return df

    def _merge_counts_annotations(count_file, merged_file):
        """Reads input files, creates temporary dataframes, and performs the merge."""
        count_df = _get_valid_dataframe(count_file, COUNTS_HEADER, sep="\t", comment="#")
        # rename the sample file path to "count"
        count_df.rename(columns={count_df.columns.tolist()[-1]:"count"}, inplace=True)
        merged_df = _get_valid_dataframe(merged_file, MERGED_HEADER, sep="\t")
        df = pd.merge(left=count_df, right=merged_df, how="left", left_on=COUNTS_HEADER[0], right_on=MERGED_HEADER[1])
        return df

    try:
        combos = json.loads(combinations)
    except json.decoder.JSONDecodeError:
        logging.critical("`combinations` was not in valid JSON format")
        sys.exit(1)

    df = _merge_counts_annotations(counts, merged)
    for name, vals in combos.items():
        if name.lower() == "taxonomy":

            # {"taxonomy": {
            #     "levels": ["phylum", "class", "order"],
            #     "KO": ["ko_id", "ko_ec"]
            #              }
            # }

            tax_levels = vals.get("levels", ["species"])

            for level in tax_levels:
                level = level.lower()

                try:
                    level_idx = TAX_LEVELS.index(level) + 1
                except ValueError:
                    logging.warning("Skipping taxonomy level %s" % level)
                    continue

                # e.g. taxonomy_phylum
                tax_name = "taxonomy_%s" % level
                if not tax_name in df.columns:
                    # convert taxonomy from full lineage to specified level
                    df[tax_name] = df["taxonomy"].apply(lambda x: ",".join(x.split(",")[0:level_idx]) if isinstance(x, str) else x)

                # print the taxonomy only table
                table_name = "%s_%s" % (name, level)
                logging.info("Writing %s table to %s_%s%s" % (table_name, prefix, table_name, suffix))
                tdf = df[[tax_name, "count"]].copy()
                tdf.dropna(how="any", thresh=2, inplace=True)
                tdf.groupby([tax_name]).sum().to_csv("%s_%s%s" % (prefix, table_name, suffix), sep="\t")

                # print taxonomy grouped with other values
                for subname, subvals in vals.items():
                    if subname.lower() == "levels": continue
                    # remove duplicates and entries not in the expected merged header
                    subvals = [i for i in list(set(subvals)) if i in MERGED_HEADER]
                    table_name = "%s_%s" % (subname, tax_name)
                    logging.info("Writing %s table to %s_%s%s" % (table_name, prefix, table_name, suffix))
                    tax_vals = subvals + [tax_name, "count"]
                    # subvals.extend([tax_name, "count"])
                    tdf = df[tax_vals].copy()
                    # has to have 'count' plus one other
                    tdf.dropna(how="any", thresh=2, inplace=True)
                    # handle one-to-many relationships
                    tdf = get_split_cols(tdf, subvals)
                    # even print empty tables
                    tdf.groupby(tax_vals[:-1]).sum().to_csv("%s_%s%s" % (prefix, table_name, suffix), sep="\t")

        else:
            logging.info("Writing %s table to %s_%s%s" % (name, prefix, name, suffix))
            # remove duplicates and entries not in the expected merged header
            vals = [i for i in list(set(vals)) if i in MERGED_HEADER]
            # adds count per combination
            vals.append("count")
            # drops unwanted columns
            tdf = df[vals].copy()
            # remove rows with no metadata; allows partial metadata
            tdf.dropna(how="any", thresh=2, inplace=True)
            # handle one-to-many relationships
            tdf = get_split_cols(tdf, vals)
            # aggregate counts and print
            tdf.groupby(vals[:-1]).sum().to_csv("%s_%s%s" % (prefix, name, suffix), sep="\t")
