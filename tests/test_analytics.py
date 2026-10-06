from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from khervelab import analytics, charts, logic
from tests.test_logic import mk_inst, nextweekday, user


@pytest.fixture
def lab(conn):
    xps = mk_inst(conn, "auto", open_time="08:00", close_time="18:00")
    bet = mk_inst(conn, "manual", name="BET", open_time="08:00", close_time="18:00")
    for i, internal, external in ((xps, 40, 80), (bet, 10, 20)):
        conn.execute("INSERT INTO rates VALUES (?, 'Internal', ?)", (i, internal))
        conn.execute("INSERT INTO rates VALUES (?, 'External academic', ?)", (i, external))
    a = user(conn, "alice")
    b = user(conn, "bob", category="External academic")
    conn.execute("UPDATE users SET group_name='Catalysis' WHERE id=?", (b,))
    logic.book(conn, xps, a, nextweekday(9), nextweekday(11))        # 2 h × 40
    logic.book(conn, xps, b, nextweekday(13), nextweekday(14))       # 1 h × 80
    logic.book(conn, bet, a, nextweekday(9), nextweekday(12))        # pending, 3 h × 10
    conn.commit()
    return {"xps": xps, "bet": bet, "alice": a, "bob": b, "day": nextweekday(0).date()}


def test_finance_figures_agree_with_logic_cost(conn, lab):
    d = lab["day"]
    a = analytics.build(conn, d, d)
    kpi = {k.label: k for k in a.kpis}
    assert kpi["Revenue"].value == "£160.00"
    assert kpi["Waiting for approval"].value == "£30.00"
    assert kpi["Hours booked"].value == "3.0 h"
    assert kpi["Active users"].value == "2"
    inst = a.charts["revenue_instrument"]
    assert dict(zip(inst.labels, inst.series[0][1])) == {"XPS": 160.0}
    cat = a.charts["revenue_category"]
    assert dict(zip(cat.labels, cat.series[0][1])) == {"External academic": 80.0,
                                                       "Internal": 80.0}
    grp = {g["group"]: g for g in a.groups}
    assert grp["Catalysis"]["revenue"] == 80.0 and grp["Group A"]["hours"] == 2.0
    status = a.charts["status"]
    assert dict(zip(status.labels, status.series[0][1])) == {"Approved": 2, "Pending": 1}


def test_utilisation_heatmap_and_filters(conn, lab):
    d = lab["day"]
    a = analytics.build(conn, d, d)
    xps = next(i for i in a.instruments if i["name"] == "XPS")
    assert xps["bookable"] == 10.0 and xps["hours"] == 3.0 and xps["utilisation"] == 30.0
    heat = a.charts["heatmap"]
    row = heat.series[d.weekday()][1]
    assert row[9] == row[10] == row[13] == 1.0 and sum(row) == 3.0
    only_bob = analytics.build(conn, d, d, user_id=lab["bob"])
    assert only_bob.kpis[0].value == "£80.00"
    only_bet = analytics.build(conn, d, d, instrument_id=lab["bet"])
    assert [i["name"] for i in only_bet.instruments] == ["BET"]
    assert only_bet.kpis[0].value == "£0.00"


def test_downtime_counts_out_of_order_hours(conn, lab):
    d = lab["day"]
    s = datetime(d.year, d.month, d.day, 14)
    logic.report_issue(conn, lab["bet"], "down", s, s + timedelta(hours=3), "pump", lab["alice"])
    a = analytics.build(conn, d, d)
    down = a.charts["downtime"]
    assert dict(zip(down.labels, down.series[0][1]))["BET"] == 3.0


def test_every_chart_draws_and_exports(conn, lab):
    d = lab["day"]
    for a in (analytics.build(conn, d, d), analytics.build(conn, d.replace(year=2001),
                                                           d.replace(year=2001))):
        for ch in a.charts.values():
            svg = charts.to_svg(ch)
            assert svg.startswith("<svg") and ch.title in svg.replace("&#x27;", "'")
            assert charts.to_drawing(ch, 400).width == 400
        assert analytics.to_pdf(a)[:4] == b"%PDF"
        assert analytics.to_xlsx(a)[:2] == b"PK"


def test_empty_period_says_so(conn):
    a = analytics.build(conn, datetime(2001, 1, 1).date(), datetime(2001, 1, 31).date())
    assert "No data for this period" in charts.to_svg(a.charts["revenue_month"])
