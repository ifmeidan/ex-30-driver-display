import QtQuick
import EX30 1.0

// Battery block: big SoC %, slim charge bar, range + battery temperature.
Item {
    id: soc
    width: 380
    height: 340

    property real shownSoc: vehicle.socPct
    Behavior on shownSoc { NumberAnimation { duration: 400 } }

    readonly property color socColor: shownSoc <= 10 ? Theme.red
                                    : shownSoc <= 20 ? Theme.amber
                                    : Theme.green

    Text {
        id: caption
        text: "BATTERY"
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 15
        font.weight: Font.DemiBold
        font.letterSpacing: Theme.trackingCaps
    }

    Item {
        id: valueRow
        y: 34
        height: 110

        Text {
            id: socValue
            anchors.baseline: parent.bottom
            text: Math.round(soc.shownSoc)
            color: Theme.text
            font.family: Theme.sans
            font.pixelSize: 108
            font.weight: Font.Light
            font.features: ({ "tnum": 1 })
            Behavior on color { ColorAnimation { duration: 400 } }
        }
        Text {
            anchors.left: socValue.right
            anchors.leftMargin: 8
            anchors.baseline: socValue.baseline
            text: "%"
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 40
            font.weight: Font.Light
        }
    }

    Item {
        id: barTrack
        y: 178
        width: 340
        height: 8

        Rectangle {
            anchors.fill: parent
            radius: 4
            color: Theme.faint
            opacity: 0.6
        }
        Rectangle {
            width: parent.width * Math.max(0, Math.min(1, soc.shownSoc / 100))
            height: parent.height
            radius: 4
            color: soc.socColor
            Behavior on color { ColorAnimation { duration: 300 } }
        }
    }

    Item {
        id: rangeRow
        y: 214
        height: 40
        visible: vehicle.rangeKm > 0

        Text {
            id: rangeValue
            anchors.baseline: parent.bottom
            text: Math.round(vehicle.rangeKm)
            color: Theme.text
            font.family: Theme.sans
            font.pixelSize: 34
            font.weight: Font.Medium
            font.features: ({ "tnum": 1 })
        }
        Text {
            anchors.left: rangeValue.right
            anchors.leftMargin: 8
            anchors.baseline: rangeValue.baseline
            text: "km range"
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 21
        }
    }

    Row {
        y: 276
        spacing: 10

        Rectangle {
            width: 9
            height: 9
            radius: 4.5
            anchors.verticalCenter: parent.verticalCenter
            color: vehicle.batteryTemp >= 42 ? Theme.red
                 : vehicle.batteryTemp >= 35 ? Theme.amber
                 : vehicle.batteryTemp <= 5 ? Theme.blue
                 : Theme.green
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: Math.round(vehicle.batteryTemp) + "°C battery"
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 21
        }
    }
}
