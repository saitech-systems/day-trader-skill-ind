#!/usr/bin/env python3
"""Builds trader-analyst's SYNTHETIC fixtures. Not real company results.

    python3 tests/fixtures/trader-analyst/make_synthetic.py

Made-up NSE companies, in the exact shapes nseindia.com's JSON answers with
(amounts are strings, in Rs lakh, like the real thing):

  results-DEMO.json              results-comparision: five quarters of Demo Industries,
                                 Q1 FY26 to Q1 FY27 (quarter ending 2026-06-30)
  financial-results-DEMO.json    corporates-financial-results: standalone and consolidated
                                 filings for the last three quarters, with XBRL links
  board-meetings-DEMO.json       corporate-board-meetings: the 2026-07-23 results meeting,
                                 a fund-raising meeting, and the 2026-10-22 results meeting
  results-DEMOBANK.json          a bank: interest earned, total income, gross NPA
  results-LAGCO.json and         a company whose results comparison stops at 2026-03-31
  financial-results-LAGCO.json   while the June quarter is already filed
  results-EMPTY.json             no rows (an unknown symbol)
  results-BLOCKED.json           the page NSE's bot protection sends instead of data
"""

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))

KEYS = ["re_amt_grs_np_asst", "re_bal_rbi_oth_bnk_funds", "re_basic_eps", "re_basic_eps_for_cont_dic_opr",
        "re_bsc_eps_bfr_exi", "re_cap_ade_rat", "re_cet_1_ret", "re_con_pro_loss", "re_create_dt", "re_curr_tax",
        "re_debt_eqt_rat", "re_debt_rdmption", "re_debt_ser_cov", "re_deff_tax", "re_depr_und_exp",
        "re_desc_note_fin", "re_desc_note_seg", "re_dil_eps_bfr_exi", "re_dilut_eps_for_cont_dic_opr",
        "re_diluted_eps", "re_excepn_items", "re_excepn_items_new", "re_extraord_items", "re_face_val",
        "re_face_value_debt", "re_from_dt", "re_goi_per_shhd", "re_grs_npa", "re_grs_npa_per", "re_inc_dre_sttr",
        "re_income_inv", "re_int_dis_adv_bills", "re_int_earned", "re_int_expd", "re_int_new", "re_int_ser_cov",
        "re_minority_int", "re_net_profit", "re_net_sale", "re_notes_to_ac", "re_oper_exp",
        "re_oper_exp_bef_pro_cont", "re_oth", "re_oth_exp", "re_oth_inc", "re_oth_inc_new", "re_oth_oper_exp",
        "re_oth_pro_cont", "re_oth_tot_exp", "re_paid_debt", "re_pdup", "re_per_grs_npa",
        "re_pro_aft_int_bef_excep", "re_pro_los_frm_dis_opr", "re_pro_loss_bef_tax", "re_pro_loss_bef_tax_sum",
        "re_prolos_dis_opr_aftr_tax", "re_proloss_ord_act", "re_prov_emp_pay", "re_pur_trd_goods",
        "re_rawmat_consump", "re_remarks", "re_res_reval", "re_res_type", "re_ret_asset", "re_seg_remarks",
        "re_seq_num", "re_share_associate", "re_staff_cost", "re_tax", "re_tax_expens_of_dis_opr", "re_to_dt",
        "re_tot_exp_exc_pro_cont", "re_tot_inc", "re_total_inc"]
QUARTER = {"06-30": ("01-APR", "30-JUN"), "09-30": ("01-JUL", "30-SEP"), "12-31": ("01-OCT", "31-DEC"),
           "03-31": ("01-JAN", "31-MAR")}

# Demo Industries, Rs lakh: (quarter end, revenue from operations, other income, total expenses,
# finance cost, exceptional items, tax, basic EPS, diluted EPS, result type, created, seq)
DEMO = [
    ("2025-06-30", 912400, 18300, 801200, 12600, 0, 33100, "14.46", "14.41", "U", "24-JUL-2025", 6100101),
    ("2025-09-30", 948700, 17900, 834300, 12400, 0, 33800, "14.78", "14.72", "U", "23-OCT-2025", 6300101),
    ("2025-12-31", 1001200, 19400, 873900, 12200, 0, 37500, "16.38", "16.31", "U", "22-JAN-2026", 6500101),
    ("2026-03-31", 1046800, 21000, 915200, 12000, -12500, 35600, "15.68", "15.61", "A", "28-APR-2026", 6700101),
    ("2026-06-30", 1021500, 46800, 893600, 11800, 0, 44200, "19.58", "19.50", "U", "23-JUL-2026", 6900101),
]
# A bank, Rs lakh: (quarter end, interest earned, other income, interest expended, operating expenses,
# provisions, tax, basic EPS, diluted EPS, gross NPA %, seq)
BANK = [
    ("2025-06-30", 512000, 98000, 301000, 141000, 42000, 31500, "6.12", "6.10", "2.41", 6100201),
    ("2025-09-30", 524500, 101200, 309800, 144300, 40100, 33000, "6.40", "6.38", "2.33", 6300201),
    ("2025-12-31", 538900, 99800, 318200, 147600, 39900, 33200, "6.44", "6.42", "2.28", 6500201),
    ("2026-03-31", 551200, 112400, 325100, 152800, 45200, 35100, "6.82", "6.80", "2.19", 6700201),
    ("2026-06-30", 560300, 104900, 331700, 155100, 38600, 34900, "6.86", "6.84", "2.12", 6900201),
]


def blank():
    return {k: None for k in KEYS}


def period(end):
    y, md = end[:4], end[5:]
    start, stop = QUARTER[md]
    return "%s-%s" % (start, y), "%s-%s" % (stop, y)


def demo_row(q):
    end, sales, other, expenses, finance, exceptional, tax, basic, diluted, kind, created, seq = q
    pbt = sales + other - expenses + exceptional
    profit = pbt - tax
    r = blank()
    r["re_from_dt"], r["re_to_dt"] = period(end)
    r.update({"re_net_sale": str(sales), "re_oth_inc_new": str(other), "re_total_inc": str(sales + other),
              "re_oth_tot_exp": str(expenses), "re_int_new": str(finance), "re_depr_und_exp": "31500",
              "re_excepn_items_new": str(exceptional), "re_pro_loss_bef_tax": str(pbt), "re_tax": str(tax),
              "re_curr_tax": str(tax - 2100), "re_deff_tax": "2100", "re_net_profit": str(profit),
              "re_con_pro_loss": str(profit), "re_proloss_ord_act": str(profit),
              "re_basic_eps_for_cont_dic_opr": basic, "re_dilut_eps_for_cont_dic_opr": diluted,
              "re_face_val": "10", "re_pdup": "66650", "re_res_type": kind, "re_create_dt": created,
              "re_seq_num": str(seq), "re_pro_los_frm_dis_opr": "0", "re_prolos_dis_opr_aftr_tax": "0",
              "re_desc_note_fin": "Synthetic test data.", "re_remarks": None})
    return r


def bank_row(q):
    end, earned, other, expended, opex, provisions, tax, basic, diluted, npa, seq = q
    pbt = earned + other - expended - opex - provisions
    r = blank()
    r["re_from_dt"], r["re_to_dt"] = period(end)
    r.update({"re_int_earned": str(earned), "re_oth_inc": str(other), "re_total_inc": str(earned + other),
              "re_int_expd": str(expended), "re_oper_exp": str(opex), "re_oth_pro_cont": str(provisions),
              "re_pro_loss_bef_tax": str(pbt), "re_tax": str(tax), "re_net_profit": str(pbt - tax),
              "re_basic_eps": basic, "re_diluted_eps": diluted, "re_grs_npa_per": npa,
              "re_face_val": "2", "re_res_type": "U", "re_create_dt": None, "re_seq_num": str(seq)})
    return r


def fy_text(end):
    """"30-Jun-2026" -> "01-Apr-2026 To 31-Mar-2027": India's April to March year."""
    y = int(end[-4:])
    y = y if end[3:6] in ("Jun", "Sep", "Dec") else y - 1
    return "01-Apr-%d To 31-Mar-%d" % (y, y + 1)


def filing(symbol, name, isin, start, end, relating, filed, consolidated, audited, seq, xid):
    tag = "C" if consolidated else "N"
    return {"audited": audited, "bank": "N", "broadCastDate": filed + ":12", "companyName": name,
            "consolidated": "Consolidated" if consolidated else "Non-Consolidated", "cumulative": "Non-cumulative",
            "difference": "00:00:31", "exchdisstime": filed + ":43", "filingDate": filed,
            "financialYear": fy_text(end),
            "format": "New", "fromDate": start, "indAs": "Ind-AS New", "industry": "-", "isin": isin,
            "oldNewFlag": "N", "params": "%s%s%sUN%sNE%s" % (start, end, relating[:2], tag, symbol),
            "period": "Quarterly", "reInd": "N", "relatingTo": relating, "resultDescription": None,
            "resultDetailedDataLink": None, "seqNumber": str(seq), "symbol": symbol, "toDate": end,
            "xbrl": "https://nsearchives.nseindia.com/corporate/xbrl/INDAS_%d_SYNTHETIC.xml" % xid}


def meeting(symbol, name, isin, day, purpose, desc, stamp, xid):
    return {"bm_symbol": symbol, "bm_date": day, "bm_purpose": purpose, "bm_desc": desc,
            "sm_indusrty": "Auto Components", "bm_timestamp": stamp, "sm_name": name, "sm_isin": isin,
            "attachment": "https://nsearchives.nseindia.com/corporate/xbrl/PRIOR_INTIMATION_%d_SYNTHETIC.xml" % xid,
            "diff": "00:00:41", "sysTime": stamp}


def write(name, doc):
    with open(os.path.join(HERE, name), "w", encoding="utf-8") as fh:
        if isinstance(doc, str):
            fh.write(doc)
        else:
            json.dump(doc, fh, indent=2)
            fh.write("\n")


def main():
    name, isin = "Demo Industries Limited", "INE000D01019"
    write("results-DEMO.json", {"resCmpData": [demo_row(q) for q in reversed(DEMO)], "bankNonBnking": "N"})
    write("financial-results-DEMO.json", [
        filing("DEMO", name, isin, "01-Apr-2026", "30-Jun-2026", "First Quarter", "23-Jul-2026 18:31", True,
               "Un-Audited", 6900102, 300101),
        filing("DEMO", name, isin, "01-Apr-2026", "30-Jun-2026", "First Quarter", "23-Jul-2026 18:28", False,
               "Un-Audited", 6900101, 300100),
        filing("DEMO", name, isin, "01-Jan-2026", "31-Mar-2026", "Fourth Quarter", "28-Apr-2026 19:02", True,
               "Audited", 6700102, 290101),
        filing("DEMO", name, isin, "01-Jan-2026", "31-Mar-2026", "Fourth Quarter", "28-Apr-2026 18:57", False,
               "Audited", 6700101, 290100),
        filing("DEMO", name, isin, "01-Oct-2025", "31-Dec-2025", "Third Quarter", "22-Jan-2026 18:40", True,
               "Un-Audited", 6500102, 280101),
        filing("DEMO", name, isin, "01-Oct-2025", "31-Dec-2025", "Third Quarter", "22-Jan-2026 18:35", False,
               "Un-Audited", 6500101, 280100),
    ])
    write("board-meetings-DEMO.json", [
        meeting("DEMO", name, isin, "22-Oct-2026",
                "Demo Industries Limited has informed the Exchange about Board Meeting to be held on 22-Oct-2026 "
                "to consider and approve the Unaudited Financial Results for the quarter ended September 30, 2026.",
                "Board Meeting Intimation", "16-Oct-2026 17:45:03", 950377),
        meeting("DEMO", name, isin, "05-Sep-2026",
                "Demo Industries Limited has informed the Exchange about Board Meeting to be held on 05-Sep-2026 "
                "to consider raising of funds through non-convertible debentures.",
                "Board Meeting Intimation", "29-Aug-2026 16:02:44", 940211),
        meeting("DEMO", name, isin, "23-Jul-2026",
                "Demo Industries Limited has informed the Exchange about Board Meeting to be held on 23-Jul-2026 "
                "to consider and approve the Unaudited Financial Results for the quarter ended June 30, 2026.",
                "Board Meeting Intimation", "14-Jul-2026 18:10:27", 930118),
    ])
    write("results-DEMOBANK.json", {"resCmpData": [bank_row(q) for q in reversed(BANK)], "bankNonBnking": "B"})
    lag = [demo_row(q) for q in reversed(DEMO[:4])]
    write("results-LAGCO.json", {"resCmpData": lag, "bankNonBnking": "N"})
    write("financial-results-LAGCO.json", [
        filing("LAGCO", "Lagging Company Limited", "INE000L01017", "01-Apr-2026", "30-Jun-2026", "First Quarter",
               "29-Jul-2026 17:15", True, "Un-Audited", 6900301, 300301),
        filing("LAGCO", "Lagging Company Limited", "INE000L01017", "01-Jan-2026", "31-Mar-2026", "Fourth Quarter",
               "30-Apr-2026 17:20", True, "Audited", 6700301, 290301),
    ])
    write("results-EMPTY.json", {"resCmpData": [], "bankNonBnking": None})
    write("results-BLOCKED.json", "<HTML><HEAD>\n<TITLE>Access Denied</TITLE>\n</HEAD><BODY>\n"
                                  "<H1>Access Denied</H1>\nYou don't have permission to access this server.\n"
                                  "</BODY>\n</HTML>\n")
    print("wrote trader-analyst fixtures to %s" % HERE)


if __name__ == "__main__":
    main()
