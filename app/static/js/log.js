(function () {
  "use strict";

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

  // keyed by activity_id string
  // value: L.Map instance, or null if no GPS
  var mapInstances = {};
  var currentOpenId = null;

  document.querySelectorAll(".activity-row").forEach(function (row) {
    row.addEventListener("click", function () {
      var activityId = row.dataset.activityId;
      var hasGps     = row.dataset.hasGps === "true";
      var isOpen     = row.classList.contains("expanded");

      // close previously open row
      if (currentOpenId && currentOpenId !== activityId) {
        var prevRow    = document.querySelector(".activity-row[data-activity-id='" + currentOpenId + "']");
        var prevDetail = document.getElementById("detail-" + currentOpenId);
        if (prevRow)    prevRow.classList.remove("expanded");
        if (prevDetail) prevDetail.style.display = "none";
      }

      if (isOpen) {
        row.classList.remove("expanded");
        document.getElementById("detail-" + activityId).style.display = "none";
        currentOpenId = null;
      } else {
        row.classList.add("expanded");
        document.getElementById("detail-" + activityId).style.display = "table-row";
        currentOpenId = activityId;

        if (activityId in mapInstances) {
          // already initialised — just fix size in case layout shifted
          if (mapInstances[activityId]) {
            setTimeout(function () {
              mapInstances[activityId].invalidateSize();
            }, 0);
          }
        } else {
          if (hasGps) {
            loadActivityMap(activityId);
          } else {
            mapInstances[activityId] = null;
          }
        }
      }
    });
  });

  function loadActivityMap(activityId) {
    fetch("/api/activity/" + activityId + "/track")
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var container = document.getElementById("mini-map-" + activityId);
        if (!container) return;

        if (!data.has_gps || !data.points || data.points.length < 2) {
          container.innerHTML =
            '<div class="no-gps-msg"><span class="no-gps-icon">📍</span>No GPS data</div>';
          mapInstances[activityId] = null;
          return;
        }

        var m = L.map("mini-map-" + activityId, { zoomControl: true, attributionControl: false });

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
          maxZoom: 19,
          attribution: "© OpenStreetMap",
        }).addTo(m);

        var color = SPORT_COLORS[data.sport_type] || "#95a5a6";
        var line  = L.polyline(data.points, {
          color:   color,
          weight:  3.5,
          opacity: 0.85,
        }).addTo(m);

        // start marker
        L.circleMarker(data.points[0], {
          radius: 5, fillColor: "#2ecc71", color: "#fff",
          weight: 2, fillOpacity: 1,
        }).addTo(m);

        // end marker
        L.circleMarker(data.points[data.points.length - 1], {
          radius: 5, fillColor: "#e74c3c", color: "#fff",
          weight: 2, fillOpacity: 1,
        }).addTo(m);

        m.fitBounds(line.getBounds(), { padding: [14, 14] });

        setTimeout(function () { m.invalidateSize(); }, 0);

        mapInstances[activityId] = m;
      })
      .catch(function (err) {
        console.error("Failed to load track for activity " + activityId, err);
        mapInstances[activityId] = null;
      });
  }
})();
