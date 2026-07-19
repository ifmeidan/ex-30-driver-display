import QtQuick
import EX30 1.0

// Charging curve: kW (y, 0–180) over SoC % (x, 0–100). Hairline grid drawn
// with cheap Rectangles; the curve itself is a Canvas repainted only when a
// new sample lands (every ~2s while charging), so GPU cost stays near zero.
Item {
    id: chart
    width: 980
    height: 310

    readonly property real maxKw: 180
    readonly property var xTicks: [0, 25, 50, 75, 100]
    readonly property var yTicks: [0, 50, 100, 150]

    function xPix(soc) { return soc / 100 * width }
    function yPix(kw) { return height - Math.min(kw, maxKw) / maxKw * height }
    // Canvas wants CSS color strings; QML color → rgba() explicitly
    function rgba(c, a) {
        return "rgba(" + Math.round(c.r * 255) + "," + Math.round(c.g * 255)
               + "," + Math.round(c.b * 255) + "," + a + ")"
    }

    // Grid
    Repeater {
        model: chart.xTicks
        Rectangle {
            required property int modelData
            x: chart.xPix(modelData)
            width: 1
            height: chart.height
            color: Theme.hairline
        }
    }
    Repeater {
        model: chart.yTicks
        Rectangle {
            required property int modelData
            y: chart.yPix(modelData)
            width: chart.width
            height: 1
            color: Theme.hairline
        }
    }

    // Tick labels
    Repeater {
        model: chart.xTicks
        Text {
            required property int modelData
            x: chart.xPix(modelData) - width / 2
            y: chart.height + 12
            text: modelData + "%"
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 16
            font.features: ({ "tnum": 1 })
        }
    }
    Repeater {
        model: chart.yTicks
        Text {
            required property int modelData
            x: -width - 16
            y: chart.yPix(modelData) - height / 2
            text: modelData
            color: Theme.subtext
            font.family: Theme.sans
            font.pixelSize: 16
            font.features: ({ "tnum": 1 })
        }
    }
    Text {
        x: -46
        y: -34
        text: "kW"
        color: Theme.subtext
        font.family: Theme.sans
        font.pixelSize: 16
    }

    Canvas {
        id: curve
        anchors.fill: parent
        antialiasing: true

        Connections {
            target: vehicle
            function onChargeHistoryChanged() { curve.requestPaint() }
        }
        Connections {
            target: Theme
            function onDayChanged() { curve.requestPaint() }
        }

        onPaint: {
            var ctx = getContext("2d")
            ctx.reset()
            var pts = vehicle.chargeHistory
            if (!pts || pts.length < 2)
                return

            // Area fill
            var grad = ctx.createLinearGradient(0, 0, 0, height)
            grad.addColorStop(0, chart.rgba(Theme.green, 0.22))
            grad.addColorStop(1, chart.rgba(Theme.green, 0.02))
            ctx.beginPath()
            ctx.moveTo(chart.xPix(pts[0][0]), height)
            for (var i = 0; i < pts.length; i++)
                ctx.lineTo(chart.xPix(pts[i][0]), chart.yPix(pts[i][1]))
            ctx.lineTo(chart.xPix(pts[pts.length - 1][0]), height)
            ctx.closePath()
            ctx.fillStyle = grad
            ctx.fill()

            // Line
            ctx.beginPath()
            for (var j = 0; j < pts.length; j++) {
                var px = chart.xPix(pts[j][0])
                var py = chart.yPix(pts[j][1])
                if (j === 0) ctx.moveTo(px, py)
                else ctx.lineTo(px, py)
            }
            ctx.strokeStyle = chart.rgba(Theme.green, 1)
            ctx.lineWidth = 3
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            ctx.stroke()
        }
    }

    // Live tip dot with pulse ring
    Item {
        id: tip
        readonly property var last: vehicle.chargeHistory.length > 0
            ? vehicle.chargeHistory[vehicle.chargeHistory.length - 1] : null
        visible: last !== null
        x: last ? chart.xPix(last[0]) : 0
        y: last ? chart.yPix(last[1]) : 0

        Rectangle {
            anchors.centerIn: parent
            width: 12
            height: 12
            radius: 6
            color: Theme.green
        }
        Rectangle {
            id: pulse
            anchors.centerIn: parent
            width: 12
            height: 12
            radius: width / 2
            color: "transparent"
            border.width: 2
            border.color: Theme.green

            ParallelAnimation {
                running: tip.visible && vehicle.isCharging
                loops: Animation.Infinite
                NumberAnimation {
                    target: pulse; property: "scale"
                    from: 1; to: 3.2; duration: 1500; easing.type: Easing.OutQuad
                }
                NumberAnimation {
                    target: pulse; property: "opacity"
                    from: 0.8; to: 0; duration: 1500
                }
            }
        }
    }
}
