import QtQuick
import EX30 1.0

// Charge-to-charge trip metrics (TripTracker) + session energy line.
Item {
    id: panel
    width: 330
    height: 300

    component TripRow: Item {
        property string label: ""
        property string value: ""
        property color valueColor: Theme.text
        width: panel.width
        height: 37

        Text {
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            text: label
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 16
            font.weight: Font.DemiBold
            font.letterSpacing: 2
        }
        Text {
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            text: value
            color: valueColor
            font.family: Theme.sans
            font.pixelSize: 24
            font.weight: Font.Medium
            font.features: ({ "tnum": 1 })
        }
    }

    function n(v, dec) { return v < 0 ? "—" : v.toFixed(dec) }

    Text {
        id: caption
        text: "TRIP"
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 15
        font.weight: Font.DemiBold
        font.letterSpacing: Theme.trackingCaps
    }

    Column {
        id: rows
        y: 36
        width: parent.width

        TripRow {
            label: "DISTANCE"
            value: vehicle.tripKm < 0 ? "—"
                 : Theme.fmtThousands(vehicle.tripKm) + " km"
        }
        TripRow {
            label: "ENERGY"
            value: panel.n(vehicle.tripKwhUsed, 1) + " kWh"
        }
        TripRow {
            label: "REGEN"
            value: panel.n(vehicle.tripRegenKwh, 1) + " kWh"
            valueColor: vehicle.tripRegenKwh > 0.05 ? Theme.green : Theme.text
        }
        TripRow {
            label: "AVERAGE"
            value: vehicle.tripEfficiency < 0 ? "—"
                 : vehicle.tripEfficiency.toFixed(1) + " kWh/100"
        }
        TripRow {
            label: "TO 10%"
            value: vehicle.rangeTo10Km < 0 ? "—"
                 : Theme.fmtThousands(vehicle.rangeTo10Km) + " km"
        }
    }

    Rectangle {
        y: rows.y + rows.height + 12
        width: parent.width
        height: 1
        color: Theme.hairline
    }

    Text {
        y: rows.y + rows.height + 24
        text: "session  " + vehicle.consumedKwh.toFixed(1) + " kWh · ↺ "
              + vehicle.regenKwh.toFixed(1)
        color: Theme.subtext
        opacity: 0.85
        font.family: Theme.sans
        font.pixelSize: 17
        font.features: ({ "tnum": 1 })
    }
}
