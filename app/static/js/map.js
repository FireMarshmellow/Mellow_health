(function () {
  var mapEl = document.getElementById("map");
  if (!mapEl) return;

  if (!TRACKS || TRACKS.length === 0) {
    mapEl.innerHTML = '<div class="alert alert-info m-3">No GPS data available yet.</div>';
    mapEl.style.height = "auto";
    return;
  }

  var SPORT_COLORS = {
    Run:        "#e74c3c",
    Ride:       "#2ecc71",
    Walk:       "#3498db",
    Hike:       "#e67e22",
    HIIT:       "#9b59b6",
    Workout:    "#9b59b6",
    Yoga:       "#1abc9c",
    Swim:       "#0dcaf0",
    CrossTrain: "#fd7e14",
  };

  var map = L.map("map");

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 19,
  }).addTo(map);

  var bounds = L.latLngBounds();

  TRACKS.forEach(function (track) {
    if (!track.points || track.points.length < 2) return;

    var color = SPORT_COLORS[track.sport_type] || "#95a5a6";

    var line = L.polyline(track.points, {
      color: color,
      weight: 2.5,
      opacity: 0.7,
    });

    line.bindTooltip(
      "<strong>" + track.name + "</strong><br><small>" + track.sport_type + "</small>",
      { sticky: true }
    );

    line.addTo(map);
    bounds.extend(line.getBounds());
  });

  if (bounds.isValid()) {
    map.fitBounds(bounds, { padding: [20, 20] });
  }
})();
