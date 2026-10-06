from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from khervelab import db, logic, mcp_hosts, mcp_server, mcp_tools
from tests.test_logic import nextweekday

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def lab(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    conn = db.connect(data / "lab.db")
    db.init(conn)
    boss = logic.create_user(conn, "manager", "managerpass", "Lab Manager", role="admin",
                             status="active")
    logic.add_instrument(conn, *logic.EXAMPLES[0])               # XPS, trained-only, 24/7
    conn.commit()
    me = conn.execute("SELECT * FROM users WHERE id=?", (boss,)).fetchone()
    log = data / "mcp-log.jsonl"
    return {"conn": conn, "me": me, "data": data,
            "call": lambda name, **a: mcp_tools.call(conn, me, name, a, log), "log": log}


def test_stdio_session(lab):
    msgs = [{"jsonrpc": "2.0", "id": 1, "method": "initialize",
             "params": {"protocolVersion": "2025-06-18", "capabilities": {}}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
             "params": {"name": "lab_overview", "arguments": {}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
             "params": {"name": "set_rates", "arguments": {"rates": [
                 {"instrument": "XPS", "category": "Martian", "rate": 1}]}}}]
    p = subprocess.run([sys.executable, "-m", "khervelab.mcp_server", "--data",
                        str(lab["data"]), "--as", "manager"], cwd=ROOT, capture_output=True,
                       text=True, input="\n".join(json.dumps(m) for m in msgs) + "\n",
                       timeout=60)
    replies = [json.loads(line) for line in p.stdout.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]          # no reply to the notification
    assert replies[0]["result"]["serverInfo"]["name"] == "khervelab"
    names = {t["name"] for t in replies[1]["result"]["tools"]}
    assert {"lab_overview", "record_past_bookings", "import_ppms"} <= names
    assert not any("delete" in n or "remove" in n for n in names)
    overview = json.loads(replies[2]["result"]["content"][0]["text"])
    assert overview["acting_as"] == "manager" and overview["instruments"][0]["name"] == "XPS"
    assert replies[3]["result"]["isError"] and "Martian" in \
        replies[3]["result"]["content"][0]["text"]


def test_only_a_manager_can_be_the_acting_user(lab):
    logic.create_user(lab["conn"], "alice", "alicepass1", "Alice", status="active")
    lab["conn"].commit()
    with pytest.raises(SystemExit, match="not an active lab manager"):
        mcp_server.Server(lab["data"], "alice")
    assert mcp_server.Server(lab["data"]).username == "manager"


def test_screenshot_workflow_users_rates_training_history(lab):
    call, conn = lab["call"], lab["conn"]
    call("add_users", users=[
        {"username": "jdoe", "full_name": "Jo Doe", "email": "jo@uni.example",
         "group": "Surfaces", "category": "Internal"},
        {"username": "acme", "full_name": "Ann Smith", "email": "ann@acme.example",
         "category": "Industry"}])
    assert logic.unclaimed(conn, "jdoe")                   # chooses a password at first login
    call("set_rates", rates=[{"instrument": "XPS", "category": "Internal", "rate": 40},
                             {"instrument": "xps", "category": "Industry", "rate": 200}])
    call("set_training", training=[{"user": "jo@uni.example", "instrument": "XPS"}])
    xps = conn.execute("SELECT id FROM instruments WHERE name='XPS'").fetchone()[0]
    jdoe = conn.execute("SELECT id FROM users WHERE username='jdoe'").fetchone()[0]
    assert logic.is_authorised(conn, jdoe, xps)
    out = call("record_past_bookings", bookings=[
        {"instrument": "XPS", "user": "jdoe", "start": "2026-02-03T09:00",
         "end": "2026-02-03T11:00", "amount": 75},
        {"instrument": "XPS", "user": "acme", "start": "2026-02-04T09:00",
         "end": "2026-02-04T10:30"}])
    assert out == {"recorded": 2, "already_there": 0, "overlapping": 0}
    rep = call("finance_report", **{"from": "2026-02-01", "to": "2026-02-28"})
    assert rep["headline"]["Revenue"]["value"] == "£375.00"          # 75 + 1.5 h × 200
    again = call("record_past_bookings", bookings=[
        {"instrument": "XPS", "user": "jdoe", "start": "2026-02-03T09:00",
         "end": "2026-02-03T11:00", "amount": 75}])
    assert again["already_there"] == 1
    logged = [json.loads(x)["tool"] for x in lab["log"].read_text().splitlines()]
    assert logged == ["add_users", "set_rates", "set_training", "record_past_bookings",
                      "record_past_bookings"]                       # reads are not logged


def test_a_bad_item_rolls_back_the_whole_batch(lab):
    with pytest.raises(mcp_tools.ToolError, match="booking 2"):
        lab["call"]("record_past_bookings", bookings=[
            {"instrument": "XPS", "user": "manager", "start": "2026-02-03T09:00",
             "end": "2026-02-03T10:00"},
            {"instrument": "XPS", "user": "nobody", "start": "2026-02-03T11:00",
             "end": "2026-02-03T12:00"}])
    assert lab["conn"].execute("SELECT COUNT(*) FROM bookings").fetchone()[0] == 0
    assert not lab["log"].exists()


def test_booking_approval_and_instrument_settings(lab):
    call, conn = lab["call"], lab["conn"]
    call("add_users", users=[{"username": "bob", "full_name": "Bob", "password": "bobpass12"}])
    call("update_instrument", instrument="XPS", changes={"approval": "manual"})
    s, e = nextweekday(9), nextweekday(11)
    q = call("quote_booking", instrument="XPS", user="bob", start=logic.fmt(s), end=logic.fmt(e))
    assert q["bookable"] and q["instant"] is True                   # booked by the manager
    made = call("book", instrument="XPS", user="bob", start=logic.fmt(s), end=logic.fmt(e))
    assert made["booked"][0]["status"] == "approved"
    bob = conn.execute("SELECT * FROM users WHERE username='bob'").fetchone()
    pending = logic.book(conn, conn.execute("SELECT id FROM instruments").fetchone()[0],
                         bob["id"], nextweekday(13), nextweekday(14))
    assert call("pending_requests")["bookings"][0]["id"] == pending.id
    call("decide_booking", booking_id=pending.id, approve=False, note="maintenance")
    assert conn.execute("SELECT status FROM bookings WHERE id=?",
                        (pending.id,)).fetchone()[0] == "rejected"
    with pytest.raises(mcp_tools.ToolError, match="approval must be"):
        call("update_instrument", instrument="XPS", changes={"approval": "sometimes"})
    with pytest.raises(mcp_tools.ToolError, match="HH:MM"):
        call("update_instrument", instrument="XPS", changes={"open_time": "9am"})
    call("update_instrument", instrument="XPS", changes={"active": 0})
    assert call("list_instruments") == []
    with pytest.raises(mcp_tools.ToolError, match="cannot demote"):
        call("update_user", user="manager", changes={"role": "user"})


def test_ppms_files_through_mcp(lab, tmp_path):
    f = tmp_path / "users.csv"
    f.write_text("Login,Full name,Email,User type\nzz,Zed Zed,zz@uni.example,Internal\n",
                 encoding="utf-8")
    prev = lab["call"]("preview_ppms_import", files=[str(f)])
    assert prev["files"][0]["kind"] == "users" and prev["would_do"]["users created"] == 1
    assert not logic.unclaimed(lab["conn"], "zz")
    done = lab["call"]("import_ppms", files=[str(f)])
    assert done["done"]["users created"] == 1 and Path(done["backup"]).exists()
    assert logic.unclaimed(lab["conn"], "zz")


def test_claude_desktop_config_is_merged_safely(tmp_path):
    cfg = tmp_path / "claude_desktop_config.json"
    cfg.write_text(json.dumps({"mcpServers": {"other": {"command": "x"}}, "theme": "dark"}))
    backup = mcp_hosts.install_desktop(tmp_path / "data", "manager", cfg)
    out = json.loads(cfg.read_text())
    assert out["theme"] == "dark" and "other" in out["mcpServers"]
    assert out["mcpServers"]["khervelab"]["args"][-2:] == ["--as", "manager"]
    assert backup.exists() and mcp_hosts.installed(cfg)
    cfg.write_text("{ not json")
    with pytest.raises(ValueError, match="by hand"):
        mcp_hosts.install_desktop(tmp_path / "data", "manager", cfg)
    assert cfg.read_text() == "{ not json"
    assert "claude mcp add --scope user khervelab" in mcp_hosts.cli_command(tmp_path, "manager")
