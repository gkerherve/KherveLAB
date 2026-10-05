/* KherveLAB published calendar. Reads window.KHERVELAB (assets/data.js) so the
   page works from GitHub Pages and from file:// alike. */
(function () {
  "use strict";
  var data = window.KHERVELAB || { events: [], instruments: {} };
  var body = document.body;
  var only = body.dataset.instrument || "";
  var STORE = "khervelab.visible";
  var boxes = Array.prototype.slice.call(document.querySelectorAll(".chips input[type=checkbox]"));

  function loadVisible() {
    try { var v = JSON.parse(localStorage.getItem(STORE) || "null"); if (Array.isArray(v)) return v; }
    catch (e) { /* storage unavailable: fall through */ }
    return Object.keys(data.instruments);
  }
  var visible = only ? [only] : loadVisible();
  boxes.forEach(function (b) { b.checked = visible.indexOf(b.value) >= 0; });

  function events() {
    return data.events.filter(function (e) { return visible.indexOf(e.instrument) >= 0; })
      .map(function (e) {
        var inst = data.instruments[e.instrument] || {};
        return { id: e.id, start: e.start, end: e.end, backgroundColor: e.color,
                 borderColor: e.color, classNames: ["kind-" + e.kind],
                 title: only ? e.title : (inst.name || e.instrument) + " · " + e.title,
                 extendedProps: e };
      });
  }

  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function requestFor(start, end) {
    var id = only;
    if (!data.github || !id) return null;
    var mins = Math.round((end - start) / 60000);
    var day = start.getFullYear() + "-" + pad(start.getMonth() + 1) + "-" + pad(start.getDate());
    var p = new URLSearchParams({ template: "booking-request.yml", labels: "booking-request",
      title: "Booking request: " + id + " " + day, instrument: id, date: day,
      start: pad(start.getHours()) + ":" + pad(start.getMinutes()),
      duration: (mins / 60).toString() });
    return "https://github.com/" + data.github + "/issues/new?" + p.toString();
  }

  var narrow = window.matchMedia("(max-width: 700px)").matches;
  var gran = only && data.instruments[only] ? data.instruments[only].granularity : 30;
  var cal = new FullCalendar.Calendar(document.getElementById("calendar"), {
    initialView: narrow ? "listWeek" : "timeGridWeek",
    headerToolbar: narrow
      ? { left: "prev,next today", center: "title", right: "listWeek,timeGridDay,dayGridMonth" }
      : { left: "prev,next today", center: "title", right: "timeGridDay,timeGridWeek,dayGridMonth,listWeek" },
    buttonText: { today: "Today", day: "Day", week: "Week", month: "Month", list: "List" },
    firstDay: 1, nowIndicator: true, allDaySlot: false, height: "auto",
    slotDuration: "00:30:00", snapDuration: { minutes: gran }, scrollTime: "07:00:00",
    slotLabelFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
    eventTimeFormat: { hour: "2-digit", minute: "2-digit", hour12: false },
    selectable: !!(only && data.github), selectMirror: true,
    select: function (info) {
      var url = requestFor(info.start, info.end);
      if (url && window.confirm("Request " + info.startStr.slice(0, 16).replace("T", " ") + " on GitHub?")) {
        window.open(url, "_blank", "noopener");
      }
      cal.unselect();
    },
    eventDidMount: function (arg) {
      var e = arg.event.extendedProps;
      arg.el.title = (data.instruments[e.instrument] || {}).name + " — " + e.title;
    },
    events: function (_info, ok) { ok(events()); }
  });
  cal.render();

  boxes.forEach(function (b) {
    b.addEventListener("change", function () {
      visible = boxes.filter(function (x) { return x.checked; }).map(function (x) { return x.value; });
      try { localStorage.setItem(STORE, JSON.stringify(visible)); } catch (e) { /* ignore */ }
      cal.refetchEvents();
    });
  });
})();
