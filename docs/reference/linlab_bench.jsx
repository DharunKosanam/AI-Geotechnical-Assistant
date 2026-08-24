import React, { useState, useEffect, useMemo, useRef, useCallback } from "react";
import {
  LayoutDashboard, Boxes, CalendarDays, Cpu, FileBarChart, ShieldCheck,
  Search, Plus, X, ArrowLeft, ArrowRight, AlertTriangle, Check, Download,
  Wrench, PackageMinus, RotateCcw, LogOut, ChevronRight, Trash2, Pencil, Users
} from "lucide-react";

/* ============================================================
   Lin Lab Bench — inventory, equipment booking, PLAXIS seats
   Single-file prototype. Seeded from the lab's own sheets.
   ============================================================ */

const CSS = `
.llb{--ink:#131A20;--ink2:#46545F;--mute:#7B8892;--line:#DCE3E1;--paper:#ECF0EF;--card:#FFFFFF;
--teal:#0B6E63;--tealS:#DFEFEC;--indigo:#2F4FA8;--indigoS:#E3E9F8;--violet:#5B3FA8;--violetS:#EAE4F8;
--amber:#9A5A08;--amberS:#F9EBD6;--crim:#9B1C31;--crimS:#FAE3E7;--rust:#B23C0C;--rustS:#FBE7DC;--gray:#68757E;--grayS:#E8ECEB;
--mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
--sans:"Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
font-family:var(--sans);color:var(--ink);background:var(--paper);min-height:100vh;-webkit-font-smoothing:antialiased;}
.llb *{box-sizing:border-box}
.llb button{font-family:inherit;cursor:pointer}
.llb input,.llb select,.llb textarea{font-family:inherit;font-size:13px;color:var(--ink);background:#fff;
border:1px solid var(--line);border-radius:5px;padding:8px 10px;width:100%;outline:none}
.llb input:focus,.llb select:focus,.llb textarea:focus{border-color:var(--teal);box-shadow:0 0 0 3px var(--tealS)}
.llb :focus-visible{outline:2px solid var(--teal);outline-offset:2px}

/* shell */
.shell{display:flex;min-height:100vh}
.rail{width:212px;flex:0 0 212px;background:var(--ink);color:#C9D3D6;padding:18px 12px;position:sticky;top:0;height:100vh;overflow:auto}
.brand{padding:4px 8px 16px}
.brand b{display:block;font-size:15px;letter-spacing:-.2px;color:#fff}
.brand span{font-family:var(--mono);font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#7E8F94}
.navb{display:flex;align-items:center;gap:9px;width:100%;background:none;border:0;color:#AEBBC0;
padding:8px 9px;border-radius:6px;font-size:13px;text-align:left;margin-bottom:1px}
.navb:hover{background:#1D2831;color:#fff}
.navb.on{background:#25333D;color:#fff}
.navb .cnt{margin-left:auto;font-family:var(--mono);font-size:10px;background:var(--crim);color:#fff;padding:1px 5px;border-radius:99px}
.main{flex:1;min-width:0;padding:22px 26px 60px}
.topbar{display:flex;align-items:flex-end;gap:14px;flex-wrap:wrap;margin-bottom:20px}
.h1{font-size:21px;font-weight:650;letter-spacing:-.4px;margin:0}
.sub{font-size:12.5px;color:var(--mute);margin:3px 0 0}
.spacer{flex:1}
.who{display:flex;align-items:center;gap:8px;background:#fff;border:1px solid var(--line);border-radius:7px;padding:6px 8px}
.who .av{width:26px;height:26px;border-radius:5px;background:var(--ink);color:#fff;display:grid;place-items:center;
font-family:var(--mono);font-size:10px;font-weight:600}

/* type utilities */
.eyebrow{font-family:var(--mono);font-size:10px;letter-spacing:.15em;text-transform:uppercase;color:var(--mute)}
.mono{font-family:var(--mono)}
.card{background:var(--card);border:1px solid var(--line);border-radius:9px}
.pad{padding:15px}
.grid{display:grid;gap:12px}
.row{display:flex;align-items:center;gap:10px}
.wrap{flex-wrap:wrap}

/* kpi */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(148px,1fr));gap:11px;margin-bottom:16px}
.kpi{background:#fff;border:1px solid var(--line);border-radius:9px;padding:13px 14px;position:relative;overflow:hidden}
.kpi .n{font-family:var(--mono);font-size:25px;font-weight:600;letter-spacing:-1px;line-height:1.1}
.kpi .l{font-size:11.5px;color:var(--mute);margin-top:3px}
.kpi i{position:absolute;left:0;top:0;bottom:0;width:3px}

/* chips */
.chip{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:550;padding:2.5px 8px;border-radius:99px;white-space:nowrap}
.dot{width:5px;height:5px;border-radius:99px;background:currentColor;opacity:.85}

/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:6px;border:1px solid var(--line);background:#fff;
color:var(--ink);border-radius:6px;padding:8px 12px;font-size:12.5px;font-weight:550}
.btn:hover{border-color:#B9C5C2;background:#FAFCFB}
.btn.p{background:var(--ink);border-color:var(--ink);color:#fff}
.btn.p:hover{background:#22303A}
.btn.g{background:var(--teal);border-color:var(--teal);color:#fff}
.btn.g:hover{filter:brightness(1.08)}
.btn.d{background:var(--crim);border-color:var(--crim);color:#fff}
.btn.sm{padding:5px 9px;font-size:11.5px}
.btn:disabled{opacity:.45;cursor:not-allowed}
.btn.ghost{border-color:transparent;background:transparent}

/* table + strata rail (signature) */
.tbl{width:100%;border-collapse:separate;border-spacing:0;font-size:12.5px}
.tbl th{text-align:left;font-family:var(--mono);font-size:9.5px;letter-spacing:.13em;text-transform:uppercase;
color:var(--mute);font-weight:600;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap;background:#fff;position:sticky;top:0;z-index:1}
.tbl td{padding:10px;border-bottom:1px solid #EEF2F1;vertical-align:middle}
.tbl tbody tr:hover td{background:#F7FAF9}
.tbl tbody tr{cursor:pointer}
.strata{width:4px;padding:0 !important}
.strata div{width:4px;height:34px;border-radius:2px}
.tscroll{overflow:auto;max-height:min(66vh,760px)}
.name{font-weight:560}
.idc{font-family:var(--mono);font-size:10.5px;color:var(--mute)}

/* filters */
.filters{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px;align-items:center}
.filters .sbox{position:relative;flex:1;min-width:190px}
.filters .sbox input{padding-left:31px}
.filters .sbox svg{position:absolute;left:9px;top:9px;color:var(--mute)}
.filters select{width:auto;min-width:118px;padding:8px 9px}

/* drawer + modal */
.veil{position:fixed;inset:0;background:rgba(16,24,30,.42);z-index:40;display:flex;justify-content:flex-end}
.drawer{width:min(520px,100%);background:var(--paper);height:100%;overflow:auto;box-shadow:-8px 0 30px rgba(0,0,0,.16)}
.dhead{background:#fff;border-bottom:1px solid var(--line);padding:16px 18px;position:sticky;top:0;z-index:2}
.dbody{padding:16px 18px 40px;display:grid;gap:12px}
.modalveil{position:fixed;inset:0;background:rgba(16,24,30,.42);z-index:60;display:grid;place-items:center;padding:16px}
.modal{background:var(--paper);border-radius:11px;width:min(520px,100%);max-height:90vh;overflow:auto}
.mhead{background:#fff;padding:14px 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:10px;position:sticky;top:0}
.mbody{padding:16px;display:grid;gap:11px}
.mfoot{display:flex;gap:8px;justify-content:flex-end;padding:0 16px 16px}
.lab{display:block;font-size:11px;font-weight:600;color:var(--ink2);margin-bottom:4px}
.lab i{color:var(--crim);font-style:normal}
.err{font-size:11px;color:var(--crim);margin-top:4px}
.two{display:grid;grid-template-columns:1fr 1fr;gap:11px}

/* spec list */
.spec{display:grid;grid-template-columns:118px 1fr;gap:6px 12px;font-size:12.5px}
.spec dt{color:var(--mute);font-size:11.5px}
.spec dd{margin:0}

/* alerts */
.alert{display:flex;gap:10px;align-items:flex-start;padding:10px 12px;border-radius:7px;font-size:12.5px;border:1px solid}
.alert b{font-weight:600}
.alert p{margin:2px 0 0;color:var(--ink2);font-size:11.5px}

/* plaxis grid (signature) */
.pg{display:grid;grid-template-columns:52px repeat(7,1fr);border:1px solid var(--line);border-radius:8px;overflow:hidden;background:#fff}
.pg .hd{background:#F6F8F8;border-bottom:1px solid var(--line);padding:7px 4px;text-align:center;font-size:11px;font-weight:600}
.pg .hd small{display:block;font-family:var(--mono);font-size:9.5px;color:var(--mute);font-weight:500}
.pg .hr{font-family:var(--mono);font-size:9.5px;color:var(--mute);text-align:right;padding:0 6px;border-right:1px solid var(--line);
display:flex;align-items:center;justify-content:flex-end;height:26px;background:#FBFCFC}
.cell{height:26px;border-right:1px solid #EEF2F1;border-bottom:1px solid #EEF2F1;position:relative;cursor:pointer;background:#fff}
.cell:hover{background:var(--tealS)}
.cell .seat{position:absolute;left:2px;right:2px;border-radius:2px;height:9px;font-size:0}
.cell .s0{top:3px}.cell .s1{top:14px}
.cell.now{box-shadow:inset 0 0 0 1px var(--amber)}
.legend{display:flex;gap:14px;flex-wrap:wrap;font-size:11.5px;color:var(--ink2);margin-top:10px;align-items:center}
.sw{width:16px;height:9px;border-radius:2px;display:inline-block;margin-right:5px;vertical-align:middle}

/* week calendar */
.wk{display:grid;grid-template-columns:repeat(7,1fr);gap:6px}
.wkd{background:#fff;border:1px solid var(--line);border-radius:7px;min-height:120px;padding:7px}
.wkd h5{margin:0 0 6px;font-size:11px;font-weight:600}
.wkd h5 span{font-family:var(--mono);font-size:9.5px;color:var(--mute);display:block}
.ev{font-size:10.5px;padding:4px 5px;border-radius:4px;margin-bottom:4px;line-height:1.3;cursor:pointer}
.ev b{display:block;font-weight:600}
.today{box-shadow:inset 0 0 0 2px var(--amber)}

/* toast */
.toasts{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:90;display:grid;gap:7px;width:min(400px,92vw)}
.toast{background:var(--ink);color:#fff;padding:10px 13px;border-radius:7px;font-size:12.5px;display:flex;gap:9px;align-items:center;
box-shadow:0 8px 24px rgba(0,0,0,.22)}
.toast.bad{background:var(--crim)}

.empty{text-align:center;padding:38px 16px;color:var(--mute);font-size:12.5px}
.empty b{display:block;color:var(--ink);font-size:13.5px;margin-bottom:4px}
.sechead{display:flex;align-items:center;gap:10px;margin:20px 0 9px}
.sechead h3{margin:0;font-size:13.5px;font-weight:620}
.sechead .rule{flex:1;height:1px;background:var(--line)}
.bar{height:7px;border-radius:99px;background:var(--grayS);overflow:hidden}
.bar i{display:block;height:100%;background:var(--teal)}

/* mobile */
.mobnav{display:none}
@media(max-width:860px){
 .rail{display:none}
 .main{padding:14px 13px 92px}
 .mobnav{display:flex;position:fixed;bottom:0;left:0;right:0;background:var(--ink);z-index:30;
  padding:6px 4px calc(6px + env(safe-area-inset-bottom));justify-content:space-around}
 .mobnav button{background:none;border:0;color:#93A2A8;display:grid;justify-items:center;gap:2px;font-size:9.5px;padding:5px 7px;border-radius:6px}
 .mobnav button.on{color:#fff;background:#25333D}
 .two{grid-template-columns:1fr}
 .wk{grid-template-columns:1fr}
 .tbl th:nth-child(n+5),.tbl td:nth-child(n+5){display:none}
 .drawer{width:100%}
 .pg{font-size:10px}
 .h1{font-size:18px}
}
`;

/* ---------------- helpers ---------------- */
const uid = (p) => p + "-" + Math.random().toString(36).slice(2, 8).toUpperCase();
const D = (s) => new Date(s);
const iso = (d) => new Date(d).toISOString();
const dayKey = (d) => new Date(d).toISOString().slice(0, 10);
const fmtD = (s) => (s ? new Date(s).toLocaleDateString("en-CA", { month: "short", day: "numeric", year: "numeric" }) : "—");
const fmtDT = (s) => (s ? new Date(s).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }) : "—");
const daysBetween = (a, b) => Math.round((D(b) - D(a)) / 864e5);
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x; };
const startOfWeek = (d) => { const x = new Date(d); const w = (x.getDay() + 6) % 7; x.setHours(0, 0, 0, 0); return addDays(x, -w); };
const NOW = () => new Date();

const STATUS = {
  "Available":         { c: "var(--teal)",   b: "var(--tealS)" },
  "In use":            { c: "var(--indigo)", b: "var(--indigoS)" },
  "Borrowed":          { c: "var(--indigo)", b: "var(--indigoS)" },
  "Reserved":          { c: "var(--violet)", b: "var(--violetS)" },
  "Under maintenance": { c: "var(--amber)",  b: "var(--amberS)" },
  "Missing":           { c: "var(--crim)",   b: "var(--crimS)" },
  "Depleted":          { c: "var(--rust)",   b: "var(--rustS)" },
  "Retired":           { c: "var(--gray)",   b: "var(--grayS)" },
};
const STATUSES = Object.keys(STATUS);
const CONDITIONS = ["New", "Good", "Fair", "Damaged", "Needs calibration", "Unserviceable"];

function Chip({ s }) {
  const t = STATUS[s] || STATUS["Retired"];
  return <span className="chip" style={{ color: t.c, background: t.b }}><span className="dot" />{s}</span>;
}

/* ---------------- seed data (from Lin Lab sheets) ---------------- */
function seed() {
  const t0 = new Date("2026-08-20T09:00:00");
  const U = (n, o) => ({ id: "U-" + String(n).padStart(3, "0"), studentId: "", group: "Lin Lab", role: "Student", cosup: "", ...o });
  const users = [
    U(1,  { name: "Dr. Cheng Lin",        email: "clin@uvic.ca",                          role: "Principal investigator", program: "Supervisor", since: "" }),
    U(2,  { name: "Subash Koirala",       email: "subash15@uvic.ca",                      role: "Lab manager", program: "PhD (fast-track)", since: "2024" }),
    // PhD students
    U(3,  { name: "Jiming Liu",           email: "kimingliu@gmail.com",                   program: "PhD", since: "2022", group: "Lin Lab — Geogrid" }),
    U(4,  { name: "Saeed Mahjoubi",       email: "mahjoubi@gmail.com",                    program: "PhD", since: "2022", group: "Lin Lab — TIP", cosup: "Dr. Min Sun" }),
    U(5,  { name: "Yongxuan Gao",         email: "asaff@live.cn",                         program: "PhD (fast-track)", since: "2023", group: "Lin Lab — DFOS", cosup: "Dr. Min Sun" }),
    U(6,  { name: "Berjees Ikra",         email: "anisaikra@gmail.com",                   program: "PhD", since: "2024" }),
    U(7,  { name: "Xinrui Zheng",         email: "xinruizheng1@uvic.ca",                  program: "PhD (fast-track)", since: "2024" }),
    U(8,  { name: "Santosh Basnet",       email: "076bce154.santosh@pcampus.edu.np",      program: "PhD (fast-track)", since: "2025" }),
    U(9,  { name: "Sangam Acharya",       email: "sangmacharya2023@gmail.com",            program: "PhD (fast-track)", since: "2025", group: "Lin Lab — Erosion" }),
    U(10, { name: "Arghya Chatterjee",    email: "arghya.chatterjee@stratumlogics.com",   program: "PhD", since: "2025", group: "Lin Lab — Stratum Logics" }),
    U(11, { name: "Dibas Thakuri",        email: "076bce070.dibas@pcampus.edu.np",        program: "PhD (fast-track)", since: "2026" }),
    U(12, { name: "Reejan Karmacharya",   email: "reejankarmacharya@uvic.ca",             program: "PhD (fast-track)", since: "2026" }),
    // MASc students
    U(13, { name: "Wanli Qi",             email: "reacherqi@163.com",                     program: "MASc", since: "2020" }),
    U(14, { name: "Davis Evans",          email: "djevans1@ualberta.ca",                  program: "MASc", since: "2021", cosup: "Dr. Min Sun" }),
    U(15, { name: "Shane Smith",          email: "",                                      program: "MASc", since: "2023", group: "Lin Lab — Numerical", cosup: "Dr. Tuna Onur" }),
    U(16, { name: "Cameron Schellenberg", email: "",                                      program: "MASc", since: "2023" }),
    U(17, { name: "Rong Deng",            email: "rongdeng@uvic.ca",                      program: "MASc", since: "2024" }),
    U(18, { name: "Hyokeon “Joseph” Lee", email: "josephlee9797@gmail.com",     program: "MASc", since: "2026" }),
    // Undergraduate researchers
    U(19, { name: "Dharun Kosanam",       email: "dharunk@uvic.ca",                       program: "Undergraduate", since: "2025" }),
    U(20, { name: "Fyn Taylor",           email: "fyntaylor@uvic.ca",                     program: "Undergraduate", since: "2026" }),
  ];

  const E = (o) => ({
    kind: "equipment", subCategory: "", description: "", manufacturer: "", model: "", serial: "",
    unit: "Nos", condition: "Good", status: "Available", custodian: "Subash Koirala",
    minStock: 0, purchaseDate: "", expiryDate: "", maintDays: 0, lastMaint: "", notes: "", qtyOut: 0, ...o,
  });

  const items = [
    E({ id: "LL-FOS-001", name: "Fiber-optic interrogator (LUNA ODiSI)", category: "Fiber optics", manufacturer: "Luna", model: "ODiSI 6100", serial: "OD6-2214", qty: 1, location: "Small room — bench 2", custodian: "Subash Koirala", status: "Borrowed", qtyOut: 1, maintDays: 180, lastMaint: "2026-04-02", purchaseDate: "2024-06-11", notes: "Shared instrument. Book through Reservations; hard cap 1 user at a time." }),
    E({ id: "LL-FOS-002", name: "Fiber optic splicer kit", category: "Fiber optics", qty: 1, location: "Small room", condition: "Good", purchaseDate: "2025-09-15", notes: "Working. Cleaver blade due for rotation." }),
    E({ id: "LL-FOS-003", name: "Pigtails (SC/APC)", category: "Fiber optics", qty: 5, qtyOut: 1, unit: "Nos", location: "With Sensors", status: "In use", purchaseDate: "2025-09-15" }),
    E({ id: "LL-FOS-004", name: "Protection sleeves", category: "Fiber optics", qty: 2, unit: "Nos", location: "With Sensors", purchaseDate: "2025-09-15" }),
    E({ id: "LL-SEN-001", name: "DFOS bare fibre — strain, 5 m", category: "Sensors", subCategory: "Strain", custodian: "Yongxuan Gao", qty: 15, qtyOut: 4, unit: "Nos", location: "Small room — sensor drawer", condition: "Fair", status: "In use", notes: "11 working / 4 damaged. Damaged units tagged red." }),
    E({ id: "LL-SEN-002", name: "DFOS bare fibre — temperature, 5 m", category: "Sensors", subCategory: "Temperature", qty: 5, unit: "Nos", location: "Small room — sensor drawer", condition: "Fair", notes: "2 working / 3 damaged." }),
    E({ id: "LL-SEN-003", name: "Rugged DFOS strain cable, 20 m", category: "Sensors", subCategory: "Strain", qty: 5, qtyOut: 3, unit: "Nos", location: "Field kit — case A", status: "In use", condition: "Good", notes: "3 deployed at field test, 2 in stock." }),
    E({ id: "LL-SEN-004", name: "EC5 soil moisture sensor (pigtail), 5 m", category: "Sensors", subCategory: "VMC", manufacturer: "METER", model: "EC-5", qty: 5, unit: "Nos", location: "Small box", status: "Reserved" }),
    E({ id: "LL-SEN-005", name: "EC5 soil moisture sensor (3.5 mm plug), 5 m", category: "Sensors", subCategory: "VMC", manufacturer: "METER", model: "EC-5", qty: 6, qtyOut: 6, unit: "Nos", location: "Small box", status: "In use" }),
    E({ id: "LL-SEN-006", name: "EC5 soil moisture sensor (pigtail), 20 m", category: "Sensors", subCategory: "VMC", manufacturer: "METER", qty: 12, qtyOut: 12, unit: "Nos", location: "Field kit — case B", status: "In use", notes: "Deployed, field tests." }),
    E({ id: "LL-SEN-007", name: "TEROS 22 water potential sensor, 20 m", category: "Sensors", subCategory: "Water potential", manufacturer: "METER", model: "TEROS 22", qty: 3, qtyOut: 3, unit: "Nos", location: "Field kit — case B", status: "In use" }),
    E({ id: "LL-SEN-008", name: "TEROS 11 temperature + water content sensor, 30 m", category: "Sensors", subCategory: "VMC/Temp", manufacturer: "METER", model: "TEROS 11", qty: 6, unit: "Nos", location: "Storage room — rack 3", notes: "Northwest WickGrid field trial." }),
    E({ id: "LL-SEN-009", name: "TEROS 31 tensiometer", category: "Sensors", subCategory: "Tensiometer", manufacturer: "METER", model: "TEROS 31", qty: 1, unit: "Nos", location: "Small room", maintDays: 90, lastMaint: "2026-06-30" }),
    E({ id: "LL-SEN-010", name: "KDE earth pressure cell, 20 m", category: "Sensors", subCategory: "Pressure cell", qty: 6, qtyOut: 6, unit: "Nos", location: "Field kit — case C", status: "In use" }),
    E({ id: "LL-SEN-011", name: "Piezoelectric bender element 1.25 × 0.5 × 0.02 in", category: "Sensors", subCategory: "Bender element test", qty: 2, unit: "Nos", location: "With Sensors", condition: "New", notes: "New — unused." }),
    E({ id: "LL-DAQ-001", name: "ZL6 datalogger", category: "Data acquisition", manufacturer: "METER", model: "ZL6", qty: 1, unit: "Nos", location: "Small room", maintDays: 365, lastMaint: "2025-08-01" }),
    E({ id: "LL-DAQ-002", name: "CR1000X datalogger", category: "Data acquisition", manufacturer: "Campbell Scientific", model: "CR1000X", qty: 1, qtyOut: 1, unit: "Nos", location: "Field site — WickGrid", status: "In use", notes: "Paired with AM16/32B." }),
    E({ id: "LL-DAQ-003", name: "AM16/32B multiplexer", category: "Data acquisition", manufacturer: "Campbell Scientific", qty: 1, qtyOut: 1, unit: "Nos", location: "Field site — WickGrid", status: "In use" }),
    E({ id: "LL-DAQ-004", name: "TEROS 31 refill station + auger", category: "Data acquisition", qty: 2, unit: "Nos", location: "Small room", condition: "Good" }),
    E({ id: "LL-GEO-001", name: "Geogrid rolls (uniaxial, assorted)", category: "Geosynthetics", custodian: "Jiming Liu", subCategory: "Geogrids", qty: 4, unit: "rolls", location: "Storage room — rack 4", notes: "Available to use." }),
    E({ id: "LL-GEO-002", name: "Geocell panels", category: "Geosynthetics", subCategory: "Geocells", qty: 12, unit: "panels", location: "Storage room", notes: "Bulk quantity." }),
    E({ id: "LL-GEO-003", name: "Geocomposite — conventional", category: "Geosynthetics", qty: 3, unit: "rolls", location: "Storage room" }),
    E({ id: "LL-GEO-004", name: "Geocomposite — WNWG", category: "Geosynthetics", qty: 2, unit: "rolls", location: "Storage room", condition: "New" }),
    E({ id: "LL-EQP-001", name: "Rotating erosion apparatus (Lin Lab build)", category: "Test apparatus", qty: 1, unit: "Nos", location: "Main lab — bay 1", status: "Under maintenance", condition: "Needs calibration", maintDays: 120, lastMaint: "2026-03-14", notes: "Torque cell drift — recalibration scheduled." }),
    E({ id: "LL-EQP-002", name: "Split mould 75 × 152 mm (undercompaction)", category: "Test apparatus", custodian: "Sangam Acharya", qty: 3, qtyOut: 1, unit: "Nos", location: "Main lab — soils bench", status: "In use", notes: "Ladd undercompaction, 8 lifts." }),
    E({ id: "LL-EQP-003", name: "MTS load frame accessories kit", category: "Test apparatus", qty: 1, unit: "Nos", location: "Main lab — bay 3", condition: "Good" }),
    // consumables
    E({ kind: "consumable", id: "LL-CON-001", name: "Nitrile gloves (M)", category: "Consumables", qty: 6, unit: "boxes", minStock: 4, location: "Storage room — shelf 1", supplier: "VWR", purchaseDate: "2026-05-02" }),
    E({ kind: "consumable", id: "LL-CON-002", name: "Ziplock bags (1 L)", category: "Consumables", qty: 2, unit: "packs", minStock: 5, location: "Storage room — shelf 1", supplier: "ULINE", purchaseDate: "2026-02-11" }),
    E({ kind: "consumable", id: "LL-CON-003", name: "Paper towels", category: "Consumables", qty: 9, unit: "rolls", minStock: 6, location: "Storage room — shelf 2", supplier: "Staples" }),
    E({ kind: "consumable", id: "LL-CON-004", name: "Cling film", category: "Consumables", qty: 1, unit: "rolls", minStock: 3, location: "Soils bench", supplier: "Staples" }),
    E({ kind: "consumable", id: "LL-CON-005", name: "Clear packing tape", category: "Consumables", qty: 0, unit: "rolls", minStock: 4, status: "Depleted", location: "Storage room — shelf 1", supplier: "Staples" }),
    E({ kind: "consumable", id: "LL-CON-006", name: "Kaolin clay (bagged)", category: "Materials", qty: 5, unit: "bags (25 kg)", minStock: 4, location: "Storage room — pallet", supplier: "Plainsman", purchaseDate: "2026-01-20" }),
    E({ kind: "consumable", id: "LL-CON-007", name: "Silica sand #40", category: "Materials", qty: 11, unit: "bags (20 kg)", minStock: 6, location: "Storage room — pallet", supplier: "Target Products" }),
    E({ kind: "consumable", id: "LL-CON-008", name: "Fibre-optic epoxy (2-part)", category: "Consumables", qty: 3, unit: "kits", minStock: 2, location: "Small room — cabinet", supplier: "Loctite", purchaseDate: "2026-03-01", expiryDate: "2026-09-10" }),
    E({ kind: "consumable", id: "LL-CON-009", name: "Calibration standard solution pH 7", category: "Consumables", qty: 2, unit: "bottles", minStock: 2, location: "Small room — cabinet", supplier: "Fisher", expiryDate: "2026-08-28" }),
    // software
    E({ kind: "software", id: "LL-SFW-001", name: "PLAXIS 2D/3D network licence", category: "Software", manufacturer: "Bentley", qty: 2, unit: "seats", location: "Network licence server", status: "In use", custodian: "Dr. Cheng Lin", notes: "2 concurrent seats. Book hourly on the PLAXIS page; log out when done or the seat is flagged overdue." }),
  ];

  const tx = [
    { id: uid("TX"), itemId: "LL-FOS-001", type: "checkout", user: "Yongxuan Gao", email: "asaff@live.cn", group: "Lin Lab — DFOS", qty: 1, ts: iso(addDays(t0, -12)), expectedReturn: iso(addDays(t0, -2)), condBefore: "Good", purpose: "Big box freeze–thaw cycle", approval: "Approved" },
    { id: uid("TX"), itemId: "LL-SEN-003", type: "checkout", user: "Yongxuan Gao", email: "asaff@live.cn", group: "Lin Lab — DFOS", qty: 3, ts: iso(addDays(t0, -12)), expectedReturn: iso(addDays(t0, 6)), condBefore: "Good", purpose: "Big box F-T cycle instrumentation", approval: "Approved" },
    { id: uid("TX"), itemId: "LL-EQP-002", type: "checkout", user: "Sangam Acharya", email: "sangmacharya2023@gmail.com", group: "Lin Lab — Erosion", qty: 1, ts: iso(addDays(t0, -4)), expectedReturn: iso(addDays(t0, 10)), condBefore: "Good", purpose: "Till specimen preparation, erosion series 3", approval: "Approved" },
    { id: uid("TX"), itemId: "LL-SEN-005", type: "checkout", user: "Jiming Liu", email: "kimingliu@gmail.com", group: "Lin Lab — Geogrid", qty: 6, ts: iso(addDays(t0, -6)), expectedReturn: iso(addDays(t0, 1)), condBefore: "Good", purpose: "MTS tests with geogrid", approval: "Approved" },
    { id: uid("TX"), itemId: "LL-FOS-003", type: "checkout", user: "Jiming Liu", email: "kimingliu@gmail.com", group: "Lin Lab — Geogrid", qty: 1, ts: iso(addDays(t0, -3)), expectedReturn: iso(addDays(t0, 4)), condBefore: "Good", purpose: "Splice terminations", approval: "Approved" },
  ];

  const res = [
    { id: uid("RS"), itemId: "LL-FOS-001", user: "Saeed Mahjoubi", group: "Lin Lab — TIP", start: iso(addDays(t0, 3)), end: iso(addDays(t0, 4)), purpose: "TIP instrumentation trial", status: "Approved", notes: "Jiming waiting behind this slot." },
    { id: uid("RS"), itemId: "LL-FOS-001", user: "Jiming Liu", group: "Lin Lab — Geogrid", start: iso(addDays(t0, 5)), end: iso(addDays(t0, 5)), purpose: "MTS tests with geogrid", status: "Pending", notes: "1–2 hours from 10 am." },
    { id: uid("RS"), itemId: "LL-EQP-003", user: "Jiming Liu", group: "Lin Lab — Geogrid", start: iso(addDays(t0, 1)), end: iso(addDays(t0, 2)), purpose: "Tensile pull-out setup", status: "Approved" },
    { id: uid("RS"), itemId: "LL-SEN-004", user: "Yongxuan Gao", group: "Lin Lab — DFOS", start: iso(addDays(t0, 6)), end: iso(addDays(t0, 9)), purpose: "Box F-T cycle 4", status: "Approved" },
  ];

  // PLAXIS hourly seat bookings, anchored on the current week
  const mon = startOfWeek(t0);
  const at = (dOff, h, dur) => ({ start: iso(new Date(addDays(mon, dOff).setHours(h, 0, 0, 0))), end: iso(new Date(addDays(mon, dOff).setHours(h + dur, 0, 0, 0))) });
  const plaxis = [
    { id: uid("PX"), seat: 0, user: "Shane Smith", group: "Lin Lab — Numerical", purpose: "MSE wall LE cross-check", loggedOut: true, ...at(0, 9, 4) },
    { id: uid("PX"), seat: 1, user: "Jiming Liu", group: "Lin Lab — Geogrid", purpose: "Geogrid pull-out FE model", loggedOut: true, ...at(0, 13, 3) },
    { id: uid("PX"), seat: 0, user: "Sangam Acharya", group: "Lin Lab — Erosion", purpose: "Embankment seepage run", loggedOut: true, ...at(1, 10, 5) },
    { id: uid("PX"), seat: 1, user: "Yongxuan Gao", group: "Lin Lab — DFOS", purpose: "Thermal consolidation trial", loggedOut: false, ...at(2, 8, 6) },
    { id: uid("PX"), seat: 0, user: "Shane Smith", group: "Lin Lab — Numerical", purpose: "Staged construction sensitivity", loggedOut: false, ...at(3, 9, 8) },
    { id: uid("PX"), seat: 1, user: "Saeed Mahjoubi", group: "Lin Lab — TIP", purpose: "TIP slope back-analysis", loggedOut: false, ...at(4, 11, 4) },
  ];

  const audit = [
    { id: uid("AU"), ts: iso(addDays(t0, -12)), actor: "Yongxuan Gao", action: "Checked out", entity: "LL-FOS-001", detail: "Qty 1 — Big box freeze–thaw cycle" },
    { id: uid("AU"), ts: iso(addDays(t0, -9)), actor: "Subash Koirala", action: "Flagged maintenance", entity: "LL-EQP-001", detail: "Torque cell drift; status → Under maintenance" },
    { id: uid("AU"), ts: iso(addDays(t0, -5)), actor: "Subash Koirala", action: "Adjusted quantity", entity: "LL-CON-005", detail: "4 → 0 rolls; status → Depleted" },
  ];

  return { items, tx, res, plaxis, users, audit, v: 3 };
}

/* ---------------- storage (shared, best effort) ---------------- */
const KEY = "linlab-bench-v4";
async function loadDB() {
  try {
    const r = await window.storage.get(KEY, true);
    if (r && r.value) return JSON.parse(r.value);
  } catch (e) { /* first run or storage unavailable */ }
  return null;
}
async function saveDB(db) {
  try { await window.storage.set(KEY, JSON.stringify(db), true); return true; } catch (e) { return false; }
}

/* ---------------- derived logic ---------------- */
function available(it) { return Math.max(0, (it.qty || 0) - (it.qtyOut || 0)); }
function nextMaint(it) { return it.maintDays && it.lastMaint ? iso(addDays(D(it.lastMaint), it.maintDays)) : ""; }
function openLoans(db) {
  return db.tx.filter(t => t.type === "checkout" && !t.actualReturn);
}
function alertsFor(db) {
  const out = [];
  const now = NOW();
  openLoans(db).forEach(t => {
    const it = db.items.find(i => i.id === t.itemId);
    if (t.expectedReturn && D(t.expectedReturn) < now) {
      out.push({ k: "overdue", sev: "crim", title: `${it ? it.name : t.itemId} is ${daysBetween(t.expectedReturn, now)} day(s) overdue`, body: `${t.user} · due ${fmtD(t.expectedReturn)}`, itemId: t.itemId });
    }
  });
  db.items.forEach(it => {
    if (it.kind === "consumable" && it.minStock > 0 && available(it) <= it.minStock)
      out.push({ k: "stock", sev: it.qty === 0 ? "crim" : "rust", title: `${it.name} at ${it.qty} ${it.unit}`, body: `Minimum stock ${it.minStock} ${it.unit} · ${it.supplier || "no supplier on file"}`, itemId: it.id });
    if (it.expiryDate && daysBetween(NOW(), it.expiryDate) <= 45)
      out.push({ k: "expiry", sev: "amber", title: `${it.name} expires ${fmtD(it.expiryDate)}`, body: `${daysBetween(NOW(), it.expiryDate)} days left · ${it.location}`, itemId: it.id });
    const nm = nextMaint(it);
    if (nm && daysBetween(NOW(), nm) <= 30)
      out.push({ k: "maint", sev: "amber", title: `${it.name} service due ${fmtD(nm)}`, body: `Last serviced ${fmtD(it.lastMaint)} · ${it.maintDays}-day interval`, itemId: it.id });
    if (it.status === "Missing" || it.condition === "Damaged")
      out.push({ k: "damage", sev: "crim", title: `${it.name} — ${it.status === "Missing" ? "reported missing" : "damaged"}`, body: it.notes || it.location, itemId: it.id });
  });
  db.plaxis.forEach(b => {
    if (!b.loggedOut && D(b.end) < now)
      out.push({ k: "plaxis", sev: "rust", title: `PLAXIS seat ${b.seat + 1} still held by ${b.user}`, body: `Session ended ${fmtDT(b.end)} without logging out`, itemId: "LL-SFW-001" });
  });
  db.res.filter(r => r.status === "Pending").forEach(r => {
    const it = db.items.find(i => i.id === r.itemId);
    out.push({ k: "approval", sev: "amber", title: `Reservation waiting for approval`, body: `${r.user} · ${it ? it.name : r.itemId} · ${fmtD(r.start)}`, itemId: r.itemId });
  });
  const rank = { crim: 0, rust: 1, amber: 2 };
  return out.sort((a, b) => rank[a.sev] - rank[b.sev]);
}

/* ---------------- tiny UI atoms ---------------- */
function Field({ label, req, error, children }) {
  return (
    <div>
      <label className="lab">{label}{req && <i> *</i>}</label>
      {children}
      {error && <div className="err">{error}</div>}
    </div>
  );
}
function Modal({ title, icon, onClose, children, footer, wide }) {
  useEffect(() => {
    const h = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h); return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modalveil" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" style={wide ? { width: "min(760px,100%)" } : undefined}>
        <div className="mhead">{icon}<b style={{ fontSize: 14 }}>{title}</b>
          <div className="spacer" />
          <button className="btn ghost sm" onClick={onClose} aria-label="Close"><X size={15} /></button>
        </div>
        <div className="mbody">{children}</div>
        {footer && <div className="mfoot">{footer}</div>}
      </div>
    </div>
  );
}
function Empty({ title, body }) { return <div className="empty"><b>{title}</b>{body}</div>; }

/* ============================================================ */
export default function LinLabBench() {
  const [db, setDb] = useState(null);
  const [page, setPage] = useState("dash");
  const [me, setMe] = useState("U-009");
  const [toasts, setToasts] = useState([]);
  const [openItem, setOpenItem] = useState(null);
  const [modal, setModal] = useState(null); // {kind, item}
  const saveTimer = useRef(null);
  const [persisted, setPersisted] = useState(null);

  useEffect(() => { (async () => { const d = await loadDB(); setDb(d || seed()); })(); }, []);

  const toast = useCallback((msg, bad) => {
    const id = uid("T");
    setToasts(t => [...t, { id, msg, bad }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3400);
  }, []);

  const user = db ? db.users.find(u => u.id === me) : null;
  const role = user ? user.role : "Student";
  const isManager = role === "Lab manager" || role === "Principal investigator";
  const isPI = role === "Principal investigator";

  // central mutation + audit
  const commit = useCallback((fn, log) => {
    setDb(prev => {
      const next = JSON.parse(JSON.stringify(prev));
      fn(next);
      if (log) next.audit.unshift({ id: uid("AU"), ts: iso(NOW()), actor: (prev.users.find(u => u.id === me) || {}).name || "Unknown", ...log });
      clearTimeout(saveTimer.current);
      saveTimer.current = setTimeout(async () => { const ok = await saveDB(next); setPersisted(ok); }, 450);
      return next;
    });
  }, [me]);

  if (!db) return <div className="llb"><style>{CSS}</style><div style={{ padding: 40 }} className="eyebrow">Loading lab records…</div></div>;

  const alerts = alertsFor(db);
  const nav = [
    { k: "dash", t: "Dashboard", icon: <LayoutDashboard size={15} />, cnt: alerts.filter(a => a.sev === "crim").length },
    { k: "inv", t: "Inventory", icon: <Boxes size={15} /> },
    { k: "res", t: "Reservations", icon: <CalendarDays size={15} />, cnt: db.res.filter(r => r.status === "Pending").length },
    { k: "plx", t: "PLAXIS seats", icon: <Cpu size={15} /> },
    { k: "rep", t: "Reports", icon: <FileBarChart size={15} /> },
    { k: "adm", t: "People & log", icon: <ShieldCheck size={15} /> },
  ];

  const P = { db, commit, user, role, isManager, isPI, toast, setModal, setOpenItem, setPage };

  return (
    <div className="llb">
      <style>{CSS}</style>
      <div className="shell">
        <aside className="rail">
          <div className="brand">
            <b>Lin Lab Bench</b>
            <span>UVic · Civil Engineering</span>
          </div>
          {nav.map(n => (
            <button key={n.k} className={"navb" + (page === n.k ? " on" : "")} onClick={() => setPage(n.k)}>
              {n.icon}{n.t}{n.cnt > 0 && <span className="cnt">{n.cnt}</span>}
            </button>
          ))}
          <div style={{ marginTop: 22, padding: "12px 9px 0", borderTop: "1px solid #26333C" }} />
          <div style={{ padding: "0 9px", fontSize: 10.5, color: "#7E8F94", lineHeight: 1.5 }}>
            <div className="eyebrow" style={{ marginBottom: 5 }}>Records</div>
            {db.items.length} items · {openLoans(db).length} on loan<br />
            {persisted === false ? "Session only — not saved" : "Shared lab record"}
          </div>
        </aside>

        <main className="main">
          <div className="topbar">
            <div>
              <h1 className="h1">{nav.find(n => n.k === page).t}</h1>
              <p className="sub">{{
                dash: "What needs attention in the lab right now.",
                inv: "Everything the lab owns, and who has it.",
                res: "Equipment bookings and approvals.",
                plx: "Two concurrent PLAXIS seats, booked by the hour.",
                rep: "Usage, availability and stock, ready to export.",
                adm: "Lab members, permissions and the full change history.",
              }[page]}</p>
            </div>
            <div className="spacer" />
            <div className="who">
              <div className="av">{user.name.split(" ").map(s => s[0]).join("").slice(0, 2)}</div>
              <div style={{ minWidth: 0 }}>
                <select value={me} onChange={e => setMe(e.target.value)} style={{ border: 0, padding: 0, fontWeight: 600, fontSize: 12.5, background: "none" }}>
                  {db.users.map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
                </select>
                <div style={{ fontSize: 10.5, color: "var(--mute)" }}>{role}</div>
              </div>
            </div>
          </div>

          {page === "dash" && <Dashboard {...P} alerts={alerts} />}
          {page === "inv" && <Inventory {...P} />}
          {page === "res" && <Reservations {...P} />}
          {page === "plx" && <Plaxis {...P} />}
          {page === "rep" && <Reports {...P} />}
          {page === "adm" && <Admin {...P} />}
        </main>
      </div>

      <nav className="mobnav">
        {nav.map(n => (
          <button key={n.k} className={page === n.k ? "on" : ""} onClick={() => setPage(n.k)}>
            {n.icon}{n.t.split(" ")[0]}
          </button>
        ))}
      </nav>

      {openItem && <ItemDrawer {...P} itemId={openItem} onClose={() => setOpenItem(null)} />}
      {modal && <ModalRouter {...P} modal={modal} onClose={() => setModal(null)} />}

      <div className="toasts">
        {toasts.map(t => <div key={t.id} className={"toast" + (t.bad ? " bad" : "")}>{t.bad ? <AlertTriangle size={15} /> : <Check size={15} />}{t.msg}</div>)}
      </div>
    </div>
  );
}

/* ---------------- Dashboard ---------------- */
function Dashboard({ db, alerts, setOpenItem, setPage, user }) {
  const loans = openLoans(db);
  const overdue = loans.filter(t => t.expectedReturn && D(t.expectedReturn) < NOW());
  const lowStock = db.items.filter(i => i.kind === "consumable" && i.minStock > 0 && available(i) <= i.minStock);
  const avail = db.items.filter(i => i.status === "Available").length;
  const mine = loans.filter(t => t.user === user.name);
  const sevCol = { crim: ["var(--crim)", "var(--crimS)"], rust: ["var(--rust)", "var(--rustS)"], amber: ["var(--amber)", "var(--amberS)"] };

  const now = NOW();
  const todayPx = db.plaxis.filter(b => dayKey(b.start) === dayKey(now));

  return (
    <>
      <div className="kpis">
        {[
          ["Items on record", db.items.length, "var(--ink)"],
          ["Available now", avail, "var(--teal)"],
          ["On loan", loans.length, "var(--indigo)"],
          ["Overdue", overdue.length, "var(--crim)"],
          ["Low or out of stock", lowStock.length, "var(--rust)"],
          ["In maintenance", db.items.filter(i => i.status === "Under maintenance").length, "var(--amber)"],
        ].map(([l, n, c]) => (
          <div className="kpi" key={l}><i style={{ background: c }} /><div className="n" style={{ color: c }}>{n}</div><div className="l">{l}</div></div>
        ))}
      </div>

      <div className="grid" style={{ gridTemplateColumns: "minmax(0,1.35fr) minmax(0,1fr)" }}>
        <div>
          <div className="sechead"><h3>Needs attention</h3><div className="rule" /><span className="eyebrow">{alerts.length} open</span></div>
          <div className="grid" style={{ gap: 8 }}>
            {alerts.length === 0 && <div className="card"><Empty title="Nothing outstanding" body="No overdue loans, low stock or service due." /></div>}
            {alerts.slice(0, 9).map((a, i) => {
              const [c, b] = sevCol[a.sev];
              return (
                <div className="alert" key={i} style={{ background: b, borderColor: c + "33", cursor: "pointer" }} onClick={() => setOpenItem(a.itemId)}>
                  <AlertTriangle size={15} style={{ color: c, flexShrink: 0, marginTop: 1 }} />
                  <div style={{ minWidth: 0 }}><b>{a.title}</b><p>{a.body}</p></div>
                  <div className="spacer" /><ChevronRight size={15} style={{ color: c, opacity: .6 }} />
                </div>
              );
            })}
          </div>
        </div>

        <div>
          <div className="sechead"><h3>Checked out to you</h3><div className="rule" /></div>
          <div className="card">
            {mine.length === 0 ? <Empty title="Nothing on loan" body="Open Inventory to check something out." /> :
              mine.map(t => {
                const it = db.items.find(i => i.id === t.itemId) || {};
                const late = t.expectedReturn && D(t.expectedReturn) < NOW();
                return (
                  <div key={t.id} className="row" style={{ padding: "11px 14px", borderBottom: "1px solid #EEF2F1", cursor: "pointer" }} onClick={() => setOpenItem(t.itemId)}>
                    <div style={{ minWidth: 0 }}>
                      <div className="name" style={{ fontSize: 12.5 }}>{it.name}</div>
                      <div style={{ fontSize: 11, color: late ? "var(--crim)" : "var(--mute)" }}>
                        {t.qty} {it.unit} · due {fmtD(t.expectedReturn)}{late ? " — overdue" : ""}
                      </div>
                    </div>
                    <div className="spacer" /><ChevronRight size={15} style={{ color: "var(--mute)" }} />
                  </div>
                );
              })}
          </div>

          <div className="sechead"><h3>PLAXIS today</h3><div className="rule" /><button className="btn sm" onClick={() => setPage("plx")}>Open board</button></div>
          <div className="card pad">
            {todayPx.length === 0 ? <div style={{ fontSize: 12.5, color: "var(--mute)" }}>Both seats free today.</div> :
              todayPx.map(b => (
                <div key={b.id} className="row" style={{ marginBottom: 8 }}>
                  <span className="chip" style={{ color: "var(--violet)", background: "var(--violetS)" }}>Seat {b.seat + 1}</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 550 }}>{b.user}</div>
                    <div className="mono" style={{ fontSize: 10.5, color: "var(--mute)" }}>
                      {new Date(b.start).toLocaleTimeString("en-CA", { hour: "numeric", minute: "2-digit" })}–{new Date(b.end).toLocaleTimeString("en-CA", { hour: "numeric", minute: "2-digit" })}
                    </div>
                  </div>
                  <div className="spacer" />
                  {!b.loggedOut && D(b.end) < NOW() && <span className="chip" style={{ color: "var(--rust)", background: "var(--rustS)" }}>Not logged out</span>}
                </div>
              ))}
          </div>
        </div>
      </div>
    </>
  );
}

/* ---------------- Inventory ---------------- */
function Inventory({ db, setOpenItem, setModal, isManager }) {
  const [q, setQ] = useState("");
  const [cat, setCat] = useState("");
  const [st, setSt] = useState("");
  const [loc, setLoc] = useState("");
  const [cond, setCond] = useState("");
  const [kind, setKind] = useState("");
  const [sort, setSort] = useState("name");

  const cats = [...new Set(db.items.map(i => i.category))].sort();
  const locs = [...new Set(db.items.map(i => i.location))].sort();

  const rows = useMemo(() => {
    let r = db.items.filter(i =>
      (!q || (i.name + " " + i.id + " " + i.category + " " + (i.model || "") + " " + (i.notes || "")).toLowerCase().includes(q.toLowerCase())) &&
      (!cat || i.category === cat) && (!st || i.status === st) && (!loc || i.location === loc) &&
      (!cond || i.condition === cond) && (!kind || i.kind === kind));
    const cmp = {
      name: (a, b) => a.name.localeCompare(b.name),
      status: (a, b) => a.status.localeCompare(b.status) || a.name.localeCompare(b.name),
      qty: (a, b) => available(b) - available(a),
      location: (a, b) => a.location.localeCompare(b.location),
      category: (a, b) => a.category.localeCompare(b.category) || a.name.localeCompare(b.name),
    }[sort];
    return r.sort(cmp);
  }, [db.items, q, cat, st, loc, cond, kind, sort]);

  return (
    <>
      <div className="filters">
        <div className="sbox"><Search size={14} /><input placeholder="Search name, ID, model, notes…" value={q} onChange={e => setQ(e.target.value)} /></div>
        <select value={kind} onChange={e => setKind(e.target.value)}><option value="">All types</option><option value="equipment">Equipment</option><option value="consumable">Consumables</option><option value="software">Software</option></select>
        <select value={cat} onChange={e => setCat(e.target.value)}><option value="">All categories</option>{cats.map(c => <option key={c}>{c}</option>)}</select>
        <select value={st} onChange={e => setSt(e.target.value)}><option value="">Any status</option>{STATUSES.map(s => <option key={s}>{s}</option>)}</select>
        <select value={loc} onChange={e => setLoc(e.target.value)}><option value="">Anywhere</option>{locs.map(c => <option key={c}>{c}</option>)}</select>
        <select value={cond} onChange={e => setCond(e.target.value)}><option value="">Any condition</option>{CONDITIONS.map(c => <option key={c}>{c}</option>)}</select>
        <select value={sort} onChange={e => setSort(e.target.value)}><option value="name">Sort: name</option><option value="status">Sort: status</option><option value="qty">Sort: available</option><option value="location">Sort: location</option><option value="category">Sort: category</option></select>
        {isManager && <button className="btn p" onClick={() => setModal({ kind: "edit", item: null })}><Plus size={14} />Add item</button>}
      </div>

      <div className="card">
        <div className="tscroll">
          <table className="tbl">
            <thead><tr>
              <th className="strata"></th><th>Item</th><th>Category</th><th>Status</th>
              <th>Available</th><th>Location</th><th>Condition</th><th>Custodian</th>
            </tr></thead>
            <tbody>
              {rows.map(i => (
                <tr key={i.id} onClick={() => setOpenItem(i.id)}>
                  <td className="strata"><div style={{ background: (STATUS[i.status] || {}).c }} /></td>
                  <td><div className="name">{i.name}</div><div className="idc">{i.id}{i.model ? " · " + i.model : ""}</div></td>
                  <td>{i.category}{i.subCategory && <div className="idc">{i.subCategory}</div>}</td>
                  <td><Chip s={i.status} /></td>
                  <td className="mono">{available(i)}<span style={{ color: "var(--mute)" }}> / {i.qty} {i.unit}</span>
                    {i.kind === "consumable" && i.minStock > 0 && (
                      <div className="bar" style={{ marginTop: 5, width: 62 }}>
                        <i style={{ width: Math.min(100, (i.qty / Math.max(i.minStock * 2, 1)) * 100) + "%", background: i.qty <= i.minStock ? "var(--rust)" : "var(--teal)" }} />
                      </div>)}
                  </td>
                  <td>{i.location}</td>
                  <td>{i.condition}</td>
                  <td>{i.custodian}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <Empty title="No items match those filters" body="Clear a filter or widen the search." />}
        </div>
      </div>
      <div style={{ marginTop: 9, fontSize: 11.5, color: "var(--mute)" }}>{rows.length} of {db.items.length} records shown. Tap a row for full details and actions.</div>
    </>
  );
}

/* ---------------- Item drawer ---------------- */
function ItemDrawer({ db, itemId, onClose, setModal, isManager, user }) {
  const it = db.items.find(i => i.id === itemId);
  if (!it) return null;
  const loans = db.tx.filter(t => t.itemId === it.id && t.type === "checkout" && !t.actualReturn);
  const myLoan = loans.find(t => t.user === user.name);
  const history = db.tx.filter(t => t.itemId === it.id).sort((a, b) => D(b.ts) - D(a.ts)).slice(0, 8);
  const resv = db.res.filter(r => r.itemId === it.id && r.status !== "Cancelled" && D(r.end) >= NOW());
  const canCheckout = available(it) > 0 && !["Under maintenance", "Missing", "Retired", "Depleted"].includes(it.status);

  return (
    <div className="veil" onMouseDown={e => e.target === e.currentTarget && onClose()}>
      <div className="drawer">
        <div className="dhead">
          <div className="row">
            <button className="btn ghost sm" onClick={onClose}><ArrowLeft size={15} /></button>
            <span className="idc">{it.id}</span><div className="spacer" /><Chip s={it.status} />
          </div>
          <h2 style={{ margin: "8px 0 3px", fontSize: 17, letterSpacing: "-.3px" }}>{it.name}</h2>
          <div style={{ fontSize: 12, color: "var(--mute)" }}>{it.category}{it.subCategory ? " · " + it.subCategory : ""} · {it.location}</div>
          <div className="row wrap" style={{ marginTop: 11 }}>
            <button className="btn g" disabled={!canCheckout} onClick={() => setModal({ kind: "checkout", item: it })}><PackageMinus size={14} />Check out</button>
            <button className="btn" disabled={!myLoan && loans.length === 0} onClick={() => setModal({ kind: "return", item: it })}><RotateCcw size={14} />Return</button>
            <button className="btn" onClick={() => setModal({ kind: "reserve", item: it })}><CalendarDays size={14} />Reserve</button>
            <button className="btn" onClick={() => setModal({ kind: "damage", item: it })}><AlertTriangle size={14} />Report issue</button>
            {isManager && <button className="btn" onClick={() => setModal({ kind: "edit", item: it })}><Pencil size={14} />Edit</button>}
            {isManager && <button className="btn" onClick={() => setModal({ kind: "adjust", item: it })}><Wrench size={14} />Adjust / service</button>}
          </div>
        </div>

        <div className="dbody">
          <div className="card pad">
            <div className="eyebrow" style={{ marginBottom: 9 }}>Record</div>
            <dl className="spec">
              <dt>Quantity</dt><dd className="mono">{available(it)} available / {it.qty} {it.unit}{it.qtyOut ? ` · ${it.qtyOut} out` : ""}</dd>
              <dt>Condition</dt><dd>{it.condition}</dd>
              <dt>Custodian</dt><dd>{it.custodian}</dd>
              {it.manufacturer && <><dt>Manufacturer</dt><dd>{it.manufacturer}</dd></>}
              {it.model && <><dt>Model</dt><dd className="mono">{it.model}</dd></>}
              {it.serial && <><dt>Serial</dt><dd className="mono">{it.serial}</dd></>}
              {it.purchaseDate && <><dt>Purchased</dt><dd>{fmtD(it.purchaseDate)}</dd></>}
              {it.kind === "consumable" && <><dt>Minimum stock</dt><dd className="mono">{it.minStock} {it.unit}</dd></>}
              {it.supplier && <><dt>Supplier</dt><dd>{it.supplier}</dd></>}
              {it.expiryDate && <><dt>Expires</dt><dd style={{ color: daysBetween(NOW(), it.expiryDate) < 45 ? "var(--crim)" : undefined }}>{fmtD(it.expiryDate)}</dd></>}
              {it.maintDays > 0 && <><dt>Service interval</dt><dd>{it.maintDays} days · last {fmtD(it.lastMaint)}</dd></>}
              {nextMaint(it) && <><dt>Next service</dt><dd>{fmtD(nextMaint(it))}</dd></>}
              {it.notes && <><dt>Notes</dt><dd>{it.notes}</dd></>}
            </dl>
          </div>

          <div className="card pad">
            <div className="eyebrow" style={{ marginBottom: 9 }}>Currently out ({loans.length})</div>
            {loans.length === 0 ? <div style={{ fontSize: 12.5, color: "var(--mute)" }}>Nothing outstanding.</div> :
              loans.map(t => {
                const late = t.expectedReturn && D(t.expectedReturn) < NOW();
                return <div key={t.id} className="row" style={{ marginBottom: 8, fontSize: 12.5 }}>
                  <div><b>{t.user}</b> <span style={{ color: "var(--mute)" }}>· {t.qty} {it.unit}</span>
                    <div style={{ fontSize: 11, color: late ? "var(--crim)" : "var(--mute)" }}>{t.purpose} · due {fmtD(t.expectedReturn)}{late ? " (overdue)" : ""}</div></div>
                </div>;
              })}
          </div>

          {resv.length > 0 && <div className="card pad">
            <div className="eyebrow" style={{ marginBottom: 9 }}>Upcoming reservations</div>
            {resv.map(r => <div key={r.id} className="row" style={{ marginBottom: 7, fontSize: 12.5 }}>
              <span className="chip" style={{ color: r.status === "Pending" ? "var(--amber)" : "var(--violet)", background: r.status === "Pending" ? "var(--amberS)" : "var(--violetS)" }}>{r.status}</span>
              <div><b>{r.user}</b><div style={{ fontSize: 11, color: "var(--mute)" }}>{fmtD(r.start)} → {fmtD(r.end)} · {r.purpose}</div></div>
            </div>)}
          </div>}

          <div className="card pad">
            <div className="eyebrow" style={{ marginBottom: 9 }}>History</div>
            {history.length === 0 ? <div style={{ fontSize: 12.5, color: "var(--mute)" }}>No transactions yet.</div> :
              history.map(t => (
                <div key={t.id} style={{ display: "flex", gap: 9, fontSize: 12, padding: "6px 0", borderBottom: "1px solid #F1F5F4" }}>
                  <span className="mono" style={{ color: "var(--mute)", flex: "0 0 78px" }}>{fmtD(t.ts)}</span>
                  <div><b style={{ textTransform: "capitalize" }}>{t.type}</b> — {t.user}
                    <div style={{ color: "var(--mute)", fontSize: 11 }}>{t.notes || t.purpose || ""}{t.condAfter ? ` · returned ${t.condAfter}` : ""}</div></div>
                </div>
              ))}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ---------------- Modals ---------------- */
function ModalRouter(props) {
  const k = props.modal.kind;
  if (k === "checkout") return <CheckoutModal {...props} />;
  if (k === "return") return <ReturnModal {...props} />;
  if (k === "reserve") return <ReserveModal {...props} />;
  if (k === "damage") return <DamageModal {...props} />;
  if (k === "edit") return <EditModal {...props} />;
  if (k === "adjust") return <AdjustModal {...props} />;
  if (k === "plaxis") return <PlaxisModal {...props} />;
  if (k === "user") return <UserModal {...props} />;
  if (k === "export") return <ExportModal {...props} />;
  return null;
}

function CheckoutModal({ db, modal, onClose, commit, user, toast }) {
  const it = modal.item;
  const [f, setF] = useState({ name: user.name, studentId: user.studentId, email: user.email, group: user.group, qty: 1, days: 7, purpose: "", cond: it.condition });
  const [e, setE] = useState({});
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });
  const conflict = db.res.find(r => r.itemId === it.id && r.status === "Approved" && r.user !== f.name && D(r.start) <= addDays(NOW(), Number(f.days)) && D(r.end) >= NOW());

  const submit = () => {
    const er = {};
    if (!f.name.trim()) er.name = "Required.";
    if (!f.email.includes("@")) er.email = "Enter a valid email.";
    if (f.studentId.trim() && !/^V?\d{6,9}$/i.test(f.studentId.trim())) er.studentId = "Use your V-number, or leave it blank.";
    if (!f.purpose.trim()) er.purpose = "Say what it's for — this is what other people read.";
    const q = Number(f.qty);
    if (!q || q < 1) er.qty = "Enter at least 1.";
    else if (q > available(it)) er.qty = `Only ${available(it)} ${it.unit} available.`;
    setE(er); if (Object.keys(er).length) return;

    commit(d => {
      const item = d.items.find(x => x.id === it.id);
      item.qtyOut = (item.qtyOut || 0) + q;
      item.status = available(item) === 0 ? (item.kind === "consumable" ? "Depleted" : "Borrowed") : "In use";
      d.tx.unshift({
        id: uid("TX"), itemId: it.id, type: "checkout", user: f.name, studentId: f.studentId, email: f.email,
        group: f.group, qty: q, ts: iso(NOW()), expectedReturn: iso(addDays(NOW(), Number(f.days))),
        condBefore: f.cond, purpose: f.purpose, approval: "Approved",
      });
    }, { action: "Checked out", entity: it.id, detail: `Qty ${q} ${it.unit} to ${f.name} — due ${fmtD(addDays(NOW(), Number(f.days)))}` });
    toast(`${it.name} checked out — due ${fmtD(addDays(NOW(), Number(f.days)))}`);
    onClose();
  };

  return (
    <Modal title="Check out" icon={<PackageMinus size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>Confirm check-out</button></>}>
      <div className="card pad row"><div><div className="name">{it.name}</div><div className="idc">{it.id} · {available(it)} {it.unit} available · {it.location}</div></div></div>
      {conflict && <div className="alert" style={{ background: "var(--amberS)", borderColor: "var(--amber)" }}>
        <AlertTriangle size={15} style={{ color: "var(--amber)" }} />
        <div><b>Reserved by {conflict.user}</b><p>{fmtD(conflict.start)} → {fmtD(conflict.end)}. Shorten your loan or talk to them first.</p></div></div>}
      <div className="two">
        <Field label="Your name" req error={e.name}><input value={f.name} onChange={set("name")} /></Field>
        <Field label="Student ID (optional)" error={e.studentId}><input value={f.studentId} onChange={set("studentId")} /></Field>
        <Field label="Email" req error={e.email}><input value={f.email} onChange={set("email")} /></Field>
        <Field label="Research group"><input value={f.group} onChange={set("group")} /></Field>
        <Field label={`Quantity (${it.unit})`} req error={e.qty}><input type="number" min="1" max={available(it)} value={f.qty} onChange={set("qty")} /></Field>
        <Field label="Return in"><select value={f.days} onChange={set("days")}>{[1, 3, 7, 14, 30, 60, 90].map(d => <option key={d} value={d}>{d} days — {fmtD(addDays(NOW(), d))}</option>)}</select></Field>
      </div>
      <Field label="Condition at pick-up"><select value={f.cond} onChange={set("cond")}>{CONDITIONS.map(c => <option key={c}>{c}</option>)}</select></Field>
      <Field label="Purpose of use" req error={e.purpose}><textarea rows="2" value={f.purpose} onChange={set("purpose")} placeholder="e.g. Big box freeze–thaw cycle, DFOS instrumentation" /></Field>
    </Modal>
  );
}

function ReturnModal({ db, modal, onClose, commit, user, toast }) {
  const it = modal.item;
  const loans = db.tx.filter(t => t.itemId === it.id && t.type === "checkout" && !t.actualReturn);
  const [txId, setTxId] = useState((loans.find(l => l.user === user.name) || loans[0] || {}).id);
  const [same, setSame] = useState("yes");
  const [cond, setCond] = useState("Good");
  const [note, setNote] = useState("");
  const [photo, setPhoto] = useState("");
  const [e, setE] = useState({});
  const loan = loans.find(l => l.id === txId);

  const submit = () => {
    const er = {};
    if (!loan) er.txId = "Pick the loan you're closing.";
    if (same === "no" && !note.trim()) er.note = "Describe what changed — this becomes the damage record.";
    setE(er); if (Object.keys(er).length) return;

    commit(d => {
      const t = d.tx.find(x => x.id === txId);
      t.actualReturn = iso(NOW()); t.condAfter = same === "yes" ? t.condBefore : cond;
      t.notes = note || t.notes;
      const item = d.items.find(x => x.id === it.id);
      item.qtyOut = Math.max(0, (item.qtyOut || 0) - t.qty);
      if (same === "no") { item.condition = cond; if (cond === "Damaged" || cond === "Unserviceable") item.status = "Under maintenance"; }
      if (item.status !== "Under maintenance" && item.status !== "Missing")
        item.status = item.qtyOut > 0 ? "In use" : (item.qty === 0 ? "Depleted" : "Available");
      d.tx.unshift({ id: uid("TX"), itemId: it.id, type: "return", user: t.user, qty: t.qty, ts: iso(NOW()), condAfter: t.condAfter, notes: note, photo });
    }, { action: same === "yes" ? "Returned" : "Returned with issue", entity: it.id, detail: `${loan.qty} ${it.unit} from ${loan.user}${same === "no" ? ` — ${cond}: ${note}` : ""}` });
    toast(same === "yes" ? `${it.name} returned` : `${it.name} returned — flagged ${cond}`, same !== "yes");
    onClose();
  };

  return (
    <Modal title="Return item" icon={<RotateCcw size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>Confirm return</button></>}>
      <div className="card pad"><div className="name">{it.name}</div><div className="idc">{it.id}</div></div>
      <Field label="Which loan" req error={e.txId}>
        <select value={txId} onChange={ev => setTxId(ev.target.value)}>
          {loans.map(l => <option key={l.id} value={l.id}>{l.user} — {l.qty} {it.unit}, out since {fmtD(l.ts)}</option>)}
        </select>
      </Field>
      <Field label="Returned in the same condition?" req>
        <select value={same} onChange={ev => setSame(ev.target.value)}>
          <option value="yes">Yes — same condition as pick-up</option>
          <option value="no">No — damaged, incomplete or missing parts</option>
        </select>
      </Field>
      {same === "no" && <>
        <Field label="Condition now" req><select value={cond} onChange={ev => setCond(ev.target.value)}>{CONDITIONS.slice(1).map(c => <option key={c}>{c}</option>)}</select></Field>
        <Field label="What happened" req error={e.note}><textarea rows="3" value={note} onChange={ev => setNote(ev.target.value)} placeholder="e.g. 2 of 5 fibres snapped at the connector during extraction" /></Field>
        <Field label="Photo (optional)"><input type="file" accept="image/*" onChange={ev => setPhoto(ev.target.files && ev.target.files[0] ? ev.target.files[0].name : "")} />
          {photo && <div className="err" style={{ color: "var(--mute)" }}>Attached: {photo} (filename recorded; file upload needs cloud storage)</div>}</Field>
      </>}
      {same === "yes" && <Field label="Notes (optional)"><textarea rows="2" value={note} onChange={ev => setNote(ev.target.value)} /></Field>}
    </Modal>
  );
}

function ReserveModal({ db, modal, onClose, commit, user, toast, isManager }) {
  const it = modal.item;
  const [f, setF] = useState({ start: dayKey(addDays(NOW(), 1)), end: dayKey(addDays(NOW(), 2)), purpose: "", notes: "" });
  const [e, setE] = useState({});
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });

  const submit = () => {
    const er = {};
    if (!f.purpose.trim()) er.purpose = "Required.";
    if (D(f.end) < D(f.start)) er.end = "End date is before the start date.";
    const clash = db.res.find(r => r.itemId === it.id && r.status !== "Cancelled" && r.status !== "Rejected" &&
      D(f.start) <= D(r.end) && D(f.end) >= D(r.start));
    if (clash) er.end = `Overlaps ${clash.user}'s booking (${fmtD(clash.start)} → ${fmtD(clash.end)}).`;
    setE(er); if (Object.keys(er).length) return;

    const status = isManager ? "Approved" : "Pending";
    commit(d => {
      d.res.unshift({ id: uid("RS"), itemId: it.id, user: user.name, group: user.group, start: iso(D(f.start)), end: iso(D(f.end)), purpose: f.purpose, notes: f.notes, status });
      const item = d.items.find(x => x.id === it.id);
      if (item.status === "Available" && status === "Approved") item.status = "Reserved";
    }, { action: "Reserved", entity: it.id, detail: `${fmtD(f.start)} → ${fmtD(f.end)} · ${status}` });
    toast(status === "Approved" ? "Reservation confirmed" : "Reservation sent for approval");
    onClose();
  };

  const existing = db.res.filter(r => r.itemId === it.id && D(r.end) >= NOW() && r.status !== "Cancelled");
  return (
    <Modal title="Reserve equipment" icon={<CalendarDays size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>Request reservation</button></>}>
      <div className="card pad"><div className="name">{it.name}</div><div className="idc">{it.id} · {it.location}</div></div>
      {existing.length > 0 && <div className="card pad">
        <div className="eyebrow" style={{ marginBottom: 7 }}>Already booked</div>
        {existing.map(r => <div key={r.id} style={{ fontSize: 12, marginBottom: 4 }}>{fmtD(r.start)} → {fmtD(r.end)} · {r.user} <span style={{ color: "var(--mute)" }}>({r.status})</span></div>)}
      </div>}
      <div className="two">
        <Field label="From" req><input type="date" value={f.start} onChange={set("start")} /></Field>
        <Field label="To" req error={e.end}><input type="date" value={f.end} onChange={set("end")} /></Field>
      </div>
      <Field label="Purpose" req error={e.purpose}><input value={f.purpose} onChange={set("purpose")} placeholder="e.g. MTS tests with geogrid" /></Field>
      <Field label="Anything others should know"><textarea rows="2" value={f.notes} onChange={set("notes")} placeholder="Possible shifts, shared setup, contact hours…" /></Field>
      {!isManager && <div style={{ fontSize: 11.5, color: "var(--mute)" }}>A lab manager approves reservations before they're final.</div>}
    </Modal>
  );
}

function DamageModal({ modal, onClose, commit, user, toast }) {
  const it = modal.item;
  const [kind, setKind] = useState("Damaged");
  const [desc, setDesc] = useState("");
  const [photo, setPhoto] = useState("");
  const [e, setE] = useState({});
  const submit = () => {
    if (!desc.trim()) { setE({ desc: "Describe the problem." }); return; }
    commit(d => {
      const item = d.items.find(x => x.id === it.id);
      if (kind === "Missing") item.status = "Missing";
      else if (kind === "Damaged") { item.condition = "Damaged"; item.status = "Under maintenance"; }
      else item.status = "Under maintenance";
      item.notes = (item.notes ? item.notes + " | " : "") + `${kind} reported ${fmtD(NOW())}: ${desc}`;
      d.tx.unshift({ id: uid("TX"), itemId: it.id, type: "damage", user: user.name, qty: 0, ts: iso(NOW()), notes: `${kind}: ${desc}`, photo });
    }, { action: `Reported ${kind.toLowerCase()}`, entity: it.id, detail: desc });
    toast(`${it.name} flagged as ${kind.toLowerCase()}`, true);
    onClose();
  };
  return (
    <Modal title="Report a problem" icon={<AlertTriangle size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn d" onClick={submit}>File report</button></>}>
      <div className="card pad"><div className="name">{it.name}</div><div className="idc">{it.id}</div></div>
      <Field label="What's wrong" req><select value={kind} onChange={e2 => setKind(e2.target.value)}>
        <option>Damaged</option><option>Missing</option><option>Incomplete — parts missing</option><option>Needs calibration</option></select></Field>
      <Field label="Description" req error={e.desc}><textarea rows="3" value={desc} onChange={e2 => setDesc(e2.target.value)} placeholder="What happened, when, and what state it's in now." /></Field>
      <Field label="Photo (optional)"><input type="file" accept="image/*" onChange={ev => setPhoto(ev.target.files && ev.target.files[0] ? ev.target.files[0].name : "")} /></Field>
    </Modal>
  );
}

function EditModal({ db, modal, onClose, commit, toast, user }) {
  const it = modal.item;
  const blank = { id: "", name: "", kind: "equipment", category: "", subCategory: "", description: "", manufacturer: "", model: "", serial: "", qty: 1, qtyOut: 0, unit: "Nos", location: "", condition: "Good", status: "Available", custodian: user.name, minStock: 0, supplier: "", purchaseDate: "", expiryDate: "", maintDays: 0, lastMaint: "", notes: "" };
  const [f, setF] = useState(it ? { ...blank, ...it } : blank);
  const [e, setE] = useState({});
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });

  const submit = () => {
    const er = {};
    if (!f.name.trim()) er.name = "Required.";
    if (!f.category.trim()) er.category = "Required.";
    if (!f.location.trim()) er.location = "Required — people need to find it.";
    if (Number(f.qty) < 0) er.qty = "Cannot be negative.";
    setE(er); if (Object.keys(er).length) return;

    const rec = { ...f, qty: Number(f.qty), minStock: Number(f.minStock), maintDays: Number(f.maintDays) };
    commit(d => {
      if (it) { const i = d.items.findIndex(x => x.id === it.id); d.items[i] = { ...d.items[i], ...rec }; }
      else {
        const pre = { equipment: "EQP", consumable: "CON", software: "SFW" }[rec.kind];
        rec.id = "LL-" + pre + "-" + String(d.items.filter(x => x.kind === rec.kind).length + 1).padStart(3, "0") + "N";
        d.items.push(rec);
      }
    }, { action: it ? "Edited item" : "Added item", entity: it ? it.id : rec.name, detail: it ? "Record fields updated" : `${rec.qty} ${rec.unit} at ${rec.location}` });
    toast(it ? "Changes saved" : `${rec.name} added to inventory`);
    onClose();
  };

  return (
    <Modal wide title={it ? "Edit item" : "Add item"} icon={<Boxes size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>{it ? "Save changes" : "Add to inventory"}</button></>}>
      <div className="two">
        <Field label="Item name" req error={e.name}><input value={f.name} onChange={set("name")} /></Field>
        <Field label="Type" req><select value={f.kind} onChange={set("kind")}><option value="equipment">Reusable equipment</option><option value="consumable">Consumable / material</option><option value="software">Software licence</option></select></Field>
        <Field label="Category" req error={e.category}><input value={f.category} onChange={set("category")} placeholder="Sensors, Fiber optics, Geosynthetics…" /></Field>
        <Field label="Sub-category"><input value={f.subCategory} onChange={set("subCategory")} /></Field>
        <Field label="Manufacturer"><input value={f.manufacturer} onChange={set("manufacturer")} /></Field>
        <Field label="Model number"><input value={f.model} onChange={set("model")} /></Field>
        <Field label="Serial number"><input value={f.serial} onChange={set("serial")} /></Field>
        <Field label="Location" req error={e.location}><input value={f.location} onChange={set("location")} /></Field>
        <Field label="Quantity" req error={e.qty}><input type="number" min="0" value={f.qty} onChange={set("qty")} /></Field>
        <Field label="Unit"><input value={f.unit} onChange={set("unit")} placeholder="Nos, rolls, boxes, m" /></Field>
        <Field label="Condition"><select value={f.condition} onChange={set("condition")}>{CONDITIONS.map(c => <option key={c}>{c}</option>)}</select></Field>
        <Field label="Availability status"><select value={f.status} onChange={set("status")}>{STATUSES.map(s => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Custodian"><input value={f.custodian} onChange={set("custodian")} /></Field>
        <Field label="Purchase date"><input type="date" value={f.purchaseDate ? dayKey(f.purchaseDate) : ""} onChange={set("purchaseDate")} /></Field>
        {f.kind === "consumable" && <><Field label="Minimum stock level"><input type="number" min="0" value={f.minStock} onChange={set("minStock")} /></Field>
          <Field label="Supplier"><input value={f.supplier} onChange={set("supplier")} /></Field>
          <Field label="Expiry date"><input type="date" value={f.expiryDate ? dayKey(f.expiryDate) : ""} onChange={set("expiryDate")} /></Field></>}
        {f.kind !== "consumable" && <><Field label="Service interval (days)"><input type="number" min="0" value={f.maintDays} onChange={set("maintDays")} /></Field>
          <Field label="Last service date"><input type="date" value={f.lastMaint ? dayKey(f.lastMaint) : ""} onChange={set("lastMaint")} /></Field></>}
      </div>
      <Field label="Notes"><textarea rows="2" value={f.notes} onChange={set("notes")} /></Field>
      {it && <div style={{ fontSize: 11.5, color: "var(--mute)" }}>Records are never deleted. To take something out of circulation, set its status to <b>Retired</b>.</div>}
    </Modal>
  );
}

function AdjustModal({ modal, onClose, commit, toast }) {
  const it = modal.item;
  const [qty, setQty] = useState(it.qty);
  const [status, setStatus] = useState(it.status);
  const [serviced, setServiced] = useState(false);
  const [why, setWhy] = useState("");
  const [e, setE] = useState({});
  const submit = () => {
    if (!why.trim()) { setE({ why: "Say why — this goes in the audit log." }); return; }
    commit(d => {
      const item = d.items.find(x => x.id === it.id);
      item.qty = Number(qty); item.status = status;
      if (serviced) { item.lastMaint = dayKey(NOW()); if (item.condition === "Needs calibration") item.condition = "Good"; }
      if (item.kind === "consumable" && Number(qty) === 0) item.status = "Depleted";
      d.tx.unshift({ id: uid("TX"), itemId: it.id, type: serviced ? "maintenance" : "adjust", user: "—", qty: Number(qty), ts: iso(NOW()), notes: why });
    }, { action: serviced ? "Logged service" : "Adjusted record", entity: it.id, detail: `Qty ${it.qty} → ${qty}; status ${it.status} → ${status}. ${why}` });
    toast("Record updated");
    onClose();
  };
  return (
    <Modal title="Adjust quantity or service" icon={<Wrench size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>Save adjustment</button></>}>
      <div className="card pad"><div className="name">{it.name}</div><div className="idc">{it.id} · currently {it.qty} {it.unit}, {it.status.toLowerCase()}</div></div>
      <div className="two">
        <Field label={`Quantity on hand (${it.unit})`}><input type="number" min="0" value={qty} onChange={e2 => setQty(e2.target.value)} /></Field>
        <Field label="Status"><select value={status} onChange={e2 => setStatus(e2.target.value)}>{STATUSES.map(s => <option key={s}>{s}</option>)}</select></Field>
      </div>
      <label className="row" style={{ fontSize: 12.5 }}>
        <input type="checkbox" style={{ width: "auto" }} checked={serviced} onChange={e2 => setServiced(e2.target.checked)} />
        Mark as serviced today{it.maintDays ? ` (next due ${fmtD(addDays(NOW(), it.maintDays))})` : ""}
      </label>
      <Field label="Reason" req error={e.why}><textarea rows="2" value={why} onChange={e2 => setWhy(e2.target.value)} placeholder="e.g. Restocked 4 boxes from the September order" /></Field>
    </Modal>
  );
}

function UserModal({ modal, onClose, commit, toast }) {
  const u = modal.item;
  const [f, setF] = useState(u || { name: "", studentId: "", email: "", role: "Student", group: "Lin Lab", program: "PhD", since: String(new Date().getFullYear()), cosup: "" });
  const [e, setE] = useState({});
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });
  const submit = () => {
    const er = {};
    if (!f.name.trim()) er.name = "Required.";
    if (!f.email.includes("@")) er.email = "Enter a valid email.";
    setE(er); if (Object.keys(er).length) return;
    commit(d => {
      if (u) { const i = d.users.findIndex(x => x.id === u.id); d.users[i] = { ...d.users[i], ...f }; }
      else d.users.push({ ...f, id: uid("U") });
    }, { action: u ? "Updated member" : "Added member", entity: f.email, detail: `${f.name} · ${f.role}` });
    toast(u ? "Member updated" : `${f.name} added`);
    onClose();
  };
  return (
    <Modal title={u ? "Edit member" : "Add member"} icon={<Users size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit}>Save</button></>}>
      <div className="two">
        <Field label="Name" req error={e.name}><input value={f.name} onChange={set("name")} /></Field>
        <Field label="Programme"><select value={f.program} onChange={set("program")}>
          {["PhD", "PhD (fast-track)", "MASc", "Undergraduate", "Supervisor", "Visiting researcher"].map(p => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Email" req error={e.email}><input value={f.email} onChange={set("email")} /></Field>
        <Field label="Started (year)"><input value={f.since} onChange={set("since")} placeholder="2026" /></Field>
        <Field label="Role" req><select value={f.role} onChange={set("role")}>
          <option>Student</option><option>Lab manager</option><option>Principal investigator</option></select></Field>
        <Field label="Student ID"><input value={f.studentId} onChange={set("studentId")} placeholder="V00…" /></Field>
      </div>
      <Field label="Research group"><input value={f.group} onChange={set("group")} /></Field>
      <Field label="Co-supervisor"><input value={f.cosup} onChange={set("cosup")} placeholder="Dr. Min Sun" /></Field>
      <div style={{ fontSize: 11.5, color: "var(--mute)" }}>Roles control what someone can do here. Sign-in still comes from UVic NetLink once this is connected.</div>
    </Modal>
  );
}

function ExportModal({ modal, onClose, toast }) {
  const { title, csv } = modal.item;
  const download = () => {
    try {
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = title.replace(/\s+/g, "_").toLowerCase() + ".csv";
      document.body.appendChild(a); a.click(); a.remove();
      toast("CSV downloaded");
    } catch (e) { toast("Download blocked here — copy the text instead", true); }
  };
  const copy = async () => {
    try { await navigator.clipboard.writeText(csv); toast("Copied to clipboard"); }
    catch (e) { toast("Select the text and copy manually", true); }
  };
  return (
    <Modal wide title={"Export — " + title} icon={<Download size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={copy}>Copy</button><button className="btn g" onClick={download}>Download CSV</button></>}>
      <textarea readOnly rows="14" value={csv} style={{ fontFamily: "var(--mono)", fontSize: 11 }} />
      <div style={{ fontSize: 11.5, color: "var(--mute)" }}>Opens cleanly in Excel. {modal.item.rows} rows.</div>
    </Modal>
  );
}

/* ---------------- Reservations ---------------- */
function Reservations({ db, commit, isManager, toast, setOpenItem, user }) {
  const [wk, setWk] = useState(0);
  const mon = addDays(startOfWeek(NOW()), wk * 7);
  const days = [...Array(7)].map((_, i) => addDays(mon, i));
  const pending = db.res.filter(r => r.status === "Pending");

  const act = (r, status) => {
    commit(d => { const x = d.res.find(y => y.id === r.id); x.status = status; },
      { action: status === "Approved" ? "Approved reservation" : "Rejected reservation", entity: r.itemId, detail: `${r.user} · ${fmtD(r.start)} → ${fmtD(r.end)}` });
    toast(`Reservation ${status.toLowerCase()}`);
  };

  return (
    <>
      {isManager && pending.length > 0 && <>
        <div className="sechead"><h3>Waiting on you</h3><div className="rule" /></div>
        <div className="grid" style={{ gap: 8, marginBottom: 6 }}>
          {pending.map(r => {
            const it = db.items.find(i => i.id === r.itemId) || {};
            return <div className="card pad row wrap" key={r.id}>
              <div style={{ minWidth: 0 }}>
                <div className="name">{it.name}</div>
                <div style={{ fontSize: 11.5, color: "var(--mute)" }}>{r.user} · {r.group} · {fmtD(r.start)} → {fmtD(r.end)} · {r.purpose}</div>
              </div>
              <div className="spacer" />
              <button className="btn sm" onClick={() => act(r, "Rejected")}>Decline</button>
              <button className="btn g sm" onClick={() => act(r, "Approved")}>Approve</button>
            </div>;
          })}
        </div>
      </>}

      <div className="sechead">
        <h3>Week of {mon.toLocaleDateString("en-CA", { month: "long", day: "numeric" })}</h3>
        <div className="rule" />
        <button className="btn sm" onClick={() => setWk(wk - 1)}><ArrowLeft size={13} /></button>
        <button className="btn sm" onClick={() => setWk(0)}>Today</button>
        <button className="btn sm" onClick={() => setWk(wk + 1)}><ArrowRight size={13} /></button>
      </div>

      <div className="wk">
        {days.map((d, i) => {
          const on = db.res.filter(r => r.status !== "Cancelled" && r.status !== "Rejected" && dayKey(r.start) <= dayKey(d) && dayKey(r.end) >= dayKey(d));
          const isToday = dayKey(d) === dayKey(NOW());
          return (
            <div className={"wkd" + (isToday ? " today" : "")} key={i}>
              <h5>{d.toLocaleDateString("en-CA", { weekday: "short" })}<span>{d.toLocaleDateString("en-CA", { month: "short", day: "numeric" })}</span></h5>
              {on.length === 0 && <div style={{ fontSize: 10.5, color: "var(--mute)" }}>Free</div>}
              {on.map(r => {
                const it = db.items.find(x => x.id === r.itemId) || {};
                const c = r.status === "Pending" ? ["var(--amber)", "var(--amberS)"] : ["var(--violet)", "var(--violetS)"];
                return <div className="ev" key={r.id} style={{ background: c[1], color: c[0] }} onClick={() => setOpenItem(r.itemId)}>
                  <b>{it.name}</b>{r.user}
                </div>;
              })}
            </div>
          );
        })}
      </div>

      <div className="sechead"><h3>All reservations</h3><div className="rule" /></div>
      <div className="card"><div className="tscroll">
        <table className="tbl">
          <thead><tr><th>Item</th><th>Booked by</th><th>From</th><th>To</th><th>Purpose</th><th>Status</th><th></th></tr></thead>
          <tbody>
            {[...db.res].sort((a, b) => D(a.start) - D(b.start)).map(r => {
              const it = db.items.find(i => i.id === r.itemId) || {};
              const mine = r.user === user.name;
              return <tr key={r.id} onClick={() => setOpenItem(r.itemId)}>
                <td><div className="name">{it.name}</div><div className="idc">{r.itemId}</div></td>
                <td>{r.user}<div className="idc">{r.group}</div></td>
                <td className="mono">{fmtD(r.start)}</td><td className="mono">{fmtD(r.end)}</td>
                <td>{r.purpose}{r.notes && <div className="idc">{r.notes}</div>}</td>
                <td><span className="chip" style={{
                  color: r.status === "Pending" ? "var(--amber)" : r.status === "Approved" ? "var(--violet)" : "var(--gray)",
                  background: r.status === "Pending" ? "var(--amberS)" : r.status === "Approved" ? "var(--violetS)" : "var(--grayS)"
                }}>{r.status}</span></td>
                <td onClick={e => e.stopPropagation()}>
                  {(mine || isManager) && r.status !== "Cancelled" &&
                    <button className="btn sm ghost" title="Cancel" onClick={() => { act(r, "Cancelled"); }}><Trash2 size={13} /></button>}
                </td>
              </tr>;
            })}
          </tbody>
        </table>
      </div></div>
    </>
  );
}

/* ---------------- PLAXIS seat board ---------------- */
const PX_START = 7, PX_END = 22, PX_SEATS = 2;
function Plaxis({ db, commit, user, toast, setModal, isManager }) {
  const [wk, setWk] = useState(0);
  const mon = addDays(startOfWeek(NOW()), wk * 7);
  const days = [...Array(7)].map((_, i) => addDays(mon, i));
  const hours = [...Array(PX_END - PX_START)].map((_, i) => PX_START + i);
  const seatCol = ["var(--teal)", "var(--indigo)"];

  const bookingsAt = (day, h) => db.plaxis.filter(b => {
    const s = D(b.start), e = D(b.end);
    const cs = new Date(day); cs.setHours(h, 0, 0, 0);
    const ce = new Date(day); ce.setHours(h + 1, 0, 0, 0);
    return s < ce && e > cs;
  });

  const mySessions = db.plaxis.filter(b => b.user === user.name && !b.loggedOut).sort((a, b) => D(a.start) - D(b.start));
  const stale = db.plaxis.filter(b => !b.loggedOut && D(b.end) < NOW());

  const endSession = (b) => {
    commit(d => { const x = d.plaxis.find(y => y.id === b.id); x.loggedOut = true; if (D(x.end) > NOW()) x.end = iso(NOW()); },
      { action: "Released PLAXIS seat", entity: "LL-SFW-001", detail: `Seat ${b.seat + 1} · ${b.user}` });
    toast(`Seat ${b.seat + 1} released`);
  };

  const now = NOW();
  const usedHours = db.plaxis.reduce((a, b) => a + (D(b.end) - D(b.start)) / 36e5, 0);

  return (
    <>
      <div className="kpis" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
        {[["Concurrent seats", PX_SEATS, "var(--ink)"],
        ["Seats in use now", bookingsAt(now, now.getHours()).filter(b => !b.loggedOut).length, "var(--indigo)"],
        ["Held past end time", stale.length, "var(--rust)"],
        ["Hours booked, all time", Math.round(usedHours), "var(--teal)"]].map(([l, n, c]) =>
          <div className="kpi" key={l}><i style={{ background: c }} /><div className="n" style={{ color: c }}>{n}</div><div className="l">{l}</div></div>)}
      </div>

      {stale.length > 0 && <div className="grid" style={{ gap: 8, marginBottom: 14 }}>
        {stale.map(b => (
          <div className="alert" key={b.id} style={{ background: "var(--rustS)", borderColor: "var(--rust)" }}>
            <AlertTriangle size={15} style={{ color: "var(--rust)" }} />
            <div><b>Seat {b.seat + 1} still checked out to {b.user}</b><p>Booked until {fmtDT(b.end)} · {b.purpose}</p></div>
            <div className="spacer" />
            {(b.user === user.name || isManager) && <button className="btn sm" onClick={() => endSession(b)}><LogOut size={13} />Release seat</button>}
          </div>
        ))}
      </div>}

      {mySessions.length > 0 && <>
        <div className="sechead"><h3>Your sessions</h3><div className="rule" /></div>
        <div className="grid" style={{ gap: 8, marginBottom: 6 }}>
          {mySessions.map(b => <div className="card pad row" key={b.id}>
            <span className="chip" style={{ color: seatCol[b.seat], background: b.seat === 0 ? "var(--tealS)" : "var(--indigoS)" }}>Seat {b.seat + 1}</span>
            <div><div className="name">{b.purpose}</div><div className="idc">{fmtDT(b.start)} → {fmtDT(b.end)}</div></div>
            <div className="spacer" />
            <button className="btn sm" onClick={() => endSession(b)}><LogOut size={13} />Log out</button>
          </div>)}
        </div>
      </>}

      <div className="sechead">
        <h3>Week of {mon.toLocaleDateString("en-CA", { month: "long", day: "numeric" })}</h3>
        <div className="rule" />
        <button className="btn sm" onClick={() => setWk(wk - 1)}><ArrowLeft size={13} /></button>
        <button className="btn sm" onClick={() => setWk(0)}>This week</button>
        <button className="btn sm" onClick={() => setWk(wk + 1)}><ArrowRight size={13} /></button>
        <button className="btn p sm" onClick={() => setModal({ kind: "plaxis", item: { day: dayKey(NOW()), hour: Math.max(PX_START, Math.min(PX_END - 1, now.getHours())) } })}><Plus size={13} />Book time</button>
      </div>

      <div className="pg">
        <div className="hd" style={{ borderRight: "1px solid var(--line)" }}></div>
        {days.map((d, i) => <div className="hd" key={i} style={dayKey(d) === dayKey(now) ? { background: "var(--amberS)" } : undefined}>
          {d.toLocaleDateString("en-CA", { weekday: "short" })}<small>{d.toLocaleDateString("en-CA", { month: "numeric", day: "numeric" })}</small></div>)}
        {hours.map(h => (
          <React.Fragment key={h}>
            <div className="hr">{String(h).padStart(2, "0")}:00</div>
            {days.map((d, i) => {
              const bs = bookingsAt(d, h);
              const isNow = dayKey(d) === dayKey(now) && h === now.getHours();
              return (
                <div className={"cell" + (isNow ? " now" : "")} key={i}
                  title={bs.length ? bs.map(b => `Seat ${b.seat + 1}: ${b.user} — ${b.purpose}`).join("\n") : "Free — click to book"}
                  onClick={() => setModal({ kind: "plaxis", item: { day: dayKey(d), hour: h } })}>
                  {bs.map(b => <div key={b.id} className={"seat s" + b.seat}
                    style={{ background: seatCol[b.seat], opacity: b.loggedOut ? .38 : 1 }} />)}
                </div>
              );
            })}
          </React.Fragment>
        ))}
      </div>
      <div className="legend">
        <span><i className="sw" style={{ background: seatCol[0] }} />Seat 1</span>
        <span><i className="sw" style={{ background: seatCol[1] }} />Seat 2</span>
        <span><i className="sw" style={{ background: seatCol[0], opacity: .38 }} />Finished / logged out</span>
        <span style={{ color: "var(--mute)" }}>Click any cell to book. Overlapping seats are refused.</span>
      </div>

      <div className="sechead"><h3>Session log</h3><div className="rule" /></div>
      <div className="card"><div className="tscroll">
        <table className="tbl">
          <thead><tr><th>User</th><th>Seat</th><th>Start</th><th>End</th><th>Purpose</th><th>State</th><th></th></tr></thead>
          <tbody>
            {[...db.plaxis].sort((a, b) => D(b.start) - D(a.start)).map(b => {
              const state = b.loggedOut ? ["Done", "var(--gray)", "var(--grayS)"]
                : D(b.end) < now ? ["Overdue", "var(--rust)", "var(--rustS)"]
                  : D(b.start) <= now ? ["Active", "var(--crim)", "var(--crimS)"] : ["Upcoming", "var(--violet)", "var(--violetS)"];
              return <tr key={b.id} style={{ cursor: "default" }}>
                <td className="name">{b.user}<div className="idc">{b.group}</div></td>
                <td className="mono">{b.seat + 1}</td>
                <td className="mono">{fmtDT(b.start)}</td><td className="mono">{fmtDT(b.end)}</td>
                <td>{b.purpose}</td>
                <td><span className="chip" style={{ color: state[1], background: state[2] }}>{state[0]}</span></td>
                <td>{!b.loggedOut && (b.user === user.name || isManager) && <button className="btn sm ghost" onClick={() => endSession(b)}><LogOut size={13} /></button>}</td>
              </tr>;
            })}
          </tbody>
        </table>
      </div></div>
    </>
  );
}

function PlaxisModal({ db, modal, onClose, commit, user, toast }) {
  const { day, hour } = modal.item;
  const [f, setF] = useState({ day, start: hour, dur: 2, purpose: "" });
  const [e, setE] = useState({});
  const set = (k) => (ev) => setF({ ...f, [k]: ev.target.value });

  const startD = new Date(new Date(f.day + "T00:00:00").setHours(Number(f.start), 0, 0, 0));
  const endD = new Date(startD.getTime() + Number(f.dur) * 36e5);
  const clashOn = (seat) => db.plaxis.some(b => b.seat === seat && D(b.start) < endD && D(b.end) > startD);
  const freeSeat = [0, 1].find(s => !clashOn(s));

  const submit = () => {
    const er = {};
    if (!f.purpose.trim()) er.purpose = "Required — others use this to decide whether to interrupt you.";
    if (freeSeat === undefined) er.dur = "Both seats are taken for part of that window. Pick another time.";
    setE(er); if (Object.keys(er).length) return;
    commit(d => {
      d.plaxis.push({ id: uid("PX"), seat: freeSeat, user: user.name, group: user.group, purpose: f.purpose, start: iso(startD), end: iso(endD), loggedOut: false });
    }, { action: "Booked PLAXIS seat", entity: "LL-SFW-001", detail: `Seat ${freeSeat + 1} · ${fmtDT(startD)} → ${fmtDT(endD)}` });
    toast(`Seat ${freeSeat + 1} booked, ${startD.toLocaleTimeString("en-CA", { hour: "numeric" })}–${endD.toLocaleTimeString("en-CA", { hour: "numeric" })}`);
    onClose();
  };

  return (
    <Modal title="Book a PLAXIS seat" icon={<Cpu size={16} />} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn g" onClick={submit} disabled={freeSeat === undefined}>Book seat</button></>}>
      <div className="card pad">
        <div className="name">PLAXIS 2D/3D network licence</div>
        <div className="idc">2 concurrent seats · Bentley · LL-SFW-001</div>
      </div>
      <div className="two">
        <Field label="Date" req><input type="date" value={f.day} onChange={set("day")} /></Field>
        <Field label="Start" req><select value={f.start} onChange={set("start")}>
          {[...Array(PX_END - PX_START)].map((_, i) => <option key={i} value={PX_START + i}>{String(PX_START + i).padStart(2, "0")}:00</option>)}</select></Field>
      </div>
      <Field label="Duration" req error={e.dur}><select value={f.dur} onChange={set("dur")}>
        {[1, 2, 3, 4, 6, 8, 12, 24, 48].map(h => <option key={h} value={h}>{h} hour{h > 1 ? "s" : ""}</option>)}</select></Field>
      <Field label="What you're running" req error={e.purpose}><input value={f.purpose} onChange={set("purpose")} placeholder="e.g. Staged construction sensitivity, 12 phases" /></Field>
      <div className="alert" style={{ background: freeSeat === undefined ? "var(--crimS)" : "var(--tealS)", borderColor: freeSeat === undefined ? "var(--crim)" : "var(--teal)" }}>
        {freeSeat === undefined ? <AlertTriangle size={15} style={{ color: "var(--crim)" }} /> : <Check size={15} style={{ color: "var(--teal)" }} />}
        <div>{freeSeat === undefined
          ? <><b>No seat free</b><p>Both licences are booked across part of {fmtDT(startD)} → {fmtDT(endD)}.</p></>
          : <><b>Seat {freeSeat + 1} is free</b><p>{fmtDT(startD)} → {fmtDT(endD)}. Log out when you finish so the seat returns to the pool.</p></>}</div>
      </div>
    </Modal>
  );
}

/* ---------------- Reports ---------------- */
function toCSV(cols, rows) {
  const esc = (v) => `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  return [cols.map(esc).join(","), ...rows.map(r => r.map(esc).join(","))].join("\n");
}
function Reports({ db, setModal, setOpenItem }) {
  const loans = openLoans(db);
  const overdue = loans.filter(t => t.expectedReturn && D(t.expectedReturn) < NOW());
  const freq = useMemo(() => {
    const m = {};
    db.tx.filter(t => t.type === "checkout").forEach(t => { m[t.itemId] = (m[t.itemId] || 0) + 1; });
    return Object.entries(m).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [db.tx]);
  const low = db.items.filter(i => i.kind === "consumable" && i.minStock > 0 && available(i) <= i.minStock);
  const broken = db.items.filter(i => i.condition === "Damaged" || i.status === "Missing" || i.status === "Under maintenance");
  const maxFreq = Math.max(1, ...freq.map(f => f[1]));

  const exp = (title, cols, rows) => setModal({ kind: "export", item: { title, csv: toCSV(cols, rows), rows: rows.length } });

  return (
    <>
      <div className="sechead"><h3>Availability by category</h3><div className="rule" />
        <button className="btn sm" onClick={() => exp("Full inventory",
          ["Item ID", "Name", "Type", "Category", "Sub-category", "Manufacturer", "Model", "Serial", "Qty", "Out", "Available", "Unit", "Location", "Condition", "Status", "Custodian", "Min stock", "Purchased", "Expiry", "Next service", "Notes"],
          db.items.map(i => [i.id, i.name, i.kind, i.category, i.subCategory, i.manufacturer, i.model, i.serial, i.qty, i.qtyOut, available(i), i.unit, i.location, i.condition, i.status, i.custodian, i.minStock, i.purchaseDate, i.expiryDate, nextMaint(i) ? dayKey(nextMaint(i)) : "", i.notes]))}>
          <Download size={13} />Export inventory</button>
      </div>
      <div className="card"><div className="tscroll"><table className="tbl">
        <thead><tr><th>Category</th><th>Records</th><th>Available</th><th>In use / out</th><th>Maintenance</th><th>Missing</th></tr></thead>
        <tbody>
          {[...new Set(db.items.map(i => i.category))].sort().map(c => {
            const s = db.items.filter(i => i.category === c);
            return <tr key={c} style={{ cursor: "default" }}>
              <td className="name">{c}</td><td className="mono">{s.length}</td>
              <td className="mono" style={{ color: "var(--teal)" }}>{s.filter(i => i.status === "Available").length}</td>
              <td className="mono" style={{ color: "var(--indigo)" }}>{s.filter(i => ["In use", "Borrowed", "Reserved"].includes(i.status)).length}</td>
              <td className="mono" style={{ color: "var(--amber)" }}>{s.filter(i => i.status === "Under maintenance").length}</td>
              <td className="mono" style={{ color: "var(--crim)" }}>{s.filter(i => i.status === "Missing").length}</td>
            </tr>;
          })}
        </tbody></table></div></div>

      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 4 }}>
        <div>
          <div className="sechead"><h3>Most borrowed</h3><div className="rule" /></div>
          <div className="card pad">
            {freq.length === 0 ? <Empty title="No check-outs yet" body="" /> : freq.map(([id, n]) => {
              const it = db.items.find(i => i.id === id) || {};
              return <div key={id} style={{ marginBottom: 10, cursor: "pointer" }} onClick={() => setOpenItem(id)}>
                <div className="row" style={{ fontSize: 12.5 }}><span className="name">{it.name}</span><div className="spacer" /><span className="mono" style={{ color: "var(--mute)" }}>{n}×</span></div>
                <div className="bar" style={{ marginTop: 4 }}><i style={{ width: (n / maxFreq) * 100 + "%" }} /></div>
              </div>;
            })}
          </div>
        </div>
        <div>
          <div className="sechead"><h3>Overdue</h3><div className="rule" />
            <button className="btn sm" onClick={() => exp("Overdue loans", ["Item", "User", "Email", "Group", "Qty", "Out since", "Due", "Days late"],
              overdue.map(t => { const i = db.items.find(x => x.id === t.itemId) || {}; return [i.name, t.user, t.email, t.group, t.qty, dayKey(t.ts), dayKey(t.expectedReturn), daysBetween(t.expectedReturn, NOW())]; }))}>
              <Download size={13} />Export</button></div>
          <div className="card">
            {overdue.length === 0 ? <Empty title="Nothing overdue" body="Every loan is inside its return window." /> :
              overdue.map(t => { const i = db.items.find(x => x.id === t.itemId) || {}; return (
              <div key={t.id} className="row" style={{ padding: "10px 14px", borderBottom: "1px solid #EEF2F1", cursor: "pointer" }} onClick={() => setOpenItem(t.itemId)}>
                <div><div className="name" style={{ fontSize: 12.5 }}>{i.name}</div>
                  <div style={{ fontSize: 11, color: "var(--mute)" }}>{t.user} · {t.email}</div></div>
                <div className="spacer" />
                <span className="chip" style={{ color: "var(--crim)", background: "var(--crimS)" }}>{daysBetween(t.expectedReturn, NOW())} d late</span>
              </div>); })}
          </div>
        </div>
      </div>

      <div className="sechead"><h3>Low stock and consumable usage</h3><div className="rule" />
        <button className="btn sm" onClick={() => exp("Low stock", ["Item", "On hand", "Unit", "Minimum", "Supplier", "Location", "Expiry"],
          low.map(i => [i.name, i.qty, i.unit, i.minStock, i.supplier, i.location, i.expiryDate]))}><Download size={13} />Export order list</button></div>
      <div className="card"><div className="tscroll"><table className="tbl">
        <thead><tr><th className="strata"></th><th>Item</th><th>On hand</th><th>Minimum</th><th>Supplier</th><th>Location</th><th>Expiry</th></tr></thead>
        <tbody>
          {db.items.filter(i => i.kind === "consumable").map(i => (
            <tr key={i.id} onClick={() => setOpenItem(i.id)}>
              <td className="strata"><div style={{ background: available(i) <= i.minStock ? "var(--rust)" : "var(--teal)" }} /></td>
              <td className="name">{i.name}</td>
              <td className="mono" style={{ color: available(i) <= i.minStock ? "var(--rust)" : undefined }}>{i.qty} {i.unit}</td>
              <td className="mono">{i.minStock}</td><td>{i.supplier || "—"}</td><td>{i.location}</td>
              <td style={{ color: i.expiryDate && daysBetween(NOW(), i.expiryDate) < 45 ? "var(--crim)" : undefined }}>{i.expiryDate ? fmtD(i.expiryDate) : "—"}</td>
            </tr>))}
        </tbody></table></div></div>

      <div className="sechead"><h3>Damaged, missing and in service</h3><div className="rule" />
        <button className="btn sm" onClick={() => exp("Maintenance and damage", ["Item", "Status", "Condition", "Location", "Last service", "Next service", "Notes"],
          broken.map(i => [i.name, i.status, i.condition, i.location, i.lastMaint, nextMaint(i) ? dayKey(nextMaint(i)) : "", i.notes]))}><Download size={13} />Export</button></div>
      <div className="card"><div className="tscroll"><table className="tbl">
        <thead><tr><th className="strata"></th><th>Item</th><th>Status</th><th>Condition</th><th>Location</th><th>Next service</th></tr></thead>
        <tbody>
          {broken.length === 0 ? <tr><td colSpan="6"><Empty title="Everything is serviceable" body="" /></td></tr> :
            broken.map(i => <tr key={i.id} onClick={() => setOpenItem(i.id)}>
              <td className="strata"><div style={{ background: (STATUS[i.status] || {}).c }} /></td>
              <td className="name">{i.name}<div className="idc">{i.notes ? i.notes.slice(0, 70) : ""}</div></td>
              <td><Chip s={i.status} /></td><td>{i.condition}</td><td>{i.location}</td>
              <td className="mono">{nextMaint(i) ? fmtD(nextMaint(i)) : "—"}</td>
            </tr>)}
        </tbody></table></div></div>
    </>
  );
}

/* ---------------- Admin: people + audit ---------------- */
function Admin({ db, setModal, isPI, isManager, user, commit, toast }) {
  const [q, setQ] = useState("");
  const log = db.audit.filter(a => !q || (a.actor + a.action + a.entity + a.detail).toLowerCase().includes(q.toLowerCase()));

  const backup = () => {
    setModal({ kind: "export", item: { title: "Full backup (JSON as CSV cell)", csv: JSON.stringify({ ...db, exportedAt: iso(NOW()) }, null, 1), rows: db.items.length + db.tx.length + db.res.length } });
  };

  return (
    <>
      <div className="sechead"><h3>Lab members</h3><div className="rule" />
        {isPI && <button className="btn p sm" onClick={() => setModal({ kind: "user", item: null })}><Plus size={13} />Add member</button>}</div>
      {!isPI && <div className="alert" style={{ background: "var(--amberS)", borderColor: "var(--amber)", marginBottom: 10 }}>
        <ShieldCheck size={15} style={{ color: "var(--amber)" }} />
        <div><b>Read-only for your role</b><p>Only the principal investigator can add members or change permissions.</p></div></div>}
      <div className="card"><div className="tscroll"><table className="tbl">
        <thead><tr><th>Name</th><th>Programme</th><th>Role</th><th>Research group</th><th>Email</th><th>Open loans</th><th></th></tr></thead>
        <tbody>
          {db.users.map(u => (
            <tr key={u.id} style={{ cursor: "default" }}>
              <td className="name">{u.name}{u.id === user.id && <span className="idc"> — you</span>}
                {u.cosup && <div className="idc">co-supervised with {u.cosup}</div>}</td>
              <td>{u.program}{u.since && <span className="idc mono"> {u.since}–</span>}</td>
              <td><span className="chip" style={{
                color: u.role === "Principal investigator" ? "var(--crim)" : u.role === "Lab manager" ? "var(--teal)" : "var(--gray)",
                background: u.role === "Principal investigator" ? "var(--crimS)" : u.role === "Lab manager" ? "var(--tealS)" : "var(--grayS)"
              }}>{u.role}</span></td>
              <td>{u.group}</td><td>{u.email || <span style={{ color: "var(--mute)" }}>not on file</span>}</td>
              <td className="mono">{openLoans(db).filter(t => t.user === u.name).length}</td>
              <td>{isPI && <button className="btn sm ghost" onClick={() => setModal({ kind: "user", item: u })}><Pencil size={13} /></button>}</td>
            </tr>))}
        </tbody></table></div></div>

      <div className="sechead"><h3>What each role can do</h3><div className="rule" /></div>
      <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
        {[["Student", ["View all inventory", "Check out and return", "Request reservations", "Book PLAXIS seats", "Report damage or shortages"]],
        ["Lab manager", ["Everything above", "Approve or decline reservations", "Add and edit items", "Adjust quantities and log service", "Release any PLAXIS seat"]],
        ["Principal investigator", ["Everything above", "Add and remove members", "Change roles and permissions", "Export records and backups"]]].map(([r, list]) => (
          <div className="card pad" key={r}>
            <div className="eyebrow" style={{ marginBottom: 8 }}>{r}</div>
            {list.map(l => <div key={l} className="row" style={{ fontSize: 12, marginBottom: 5, alignItems: "flex-start" }}>
              <Check size={13} style={{ color: "var(--teal)", flexShrink: 0, marginTop: 2 }} />{l}</div>)}
          </div>))}
      </div>

      <div className="sechead"><h3>Activity and audit log</h3><div className="rule" />
        <button className="btn sm" onClick={backup} disabled={!isManager}><Download size={13} />Backup all records</button></div>
      <div className="filters"><div className="sbox"><Search size={14} /><input placeholder="Search the log by person, item or action…" value={q} onChange={e => setQ(e.target.value)} /></div></div>
      <div className="card"><div className="tscroll"><table className="tbl">
        <thead><tr><th>When</th><th>Who</th><th>Action</th><th>Record</th><th>Detail</th></tr></thead>
        <tbody>
          {log.map(a => (
            <tr key={a.id} style={{ cursor: "default" }}>
              <td className="mono" style={{ whiteSpace: "nowrap" }}>{fmtDT(a.ts)}</td>
              <td className="name">{a.actor}</td><td>{a.action}</td>
              <td className="mono" style={{ fontSize: 10.5 }}>{a.entity}</td><td style={{ color: "var(--ink2)" }}>{a.detail}</td>
            </tr>))}
        </tbody></table></div>
        {log.length === 0 && <Empty title="No matching entries" body="Every change to inventory, loans and bookings is recorded here." />}
      </div>
    </>
  );
}
