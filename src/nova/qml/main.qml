import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "."

ApplicationWindow {
    id: root

    visible: true
    width: 880
    height: 680
    minimumWidth: 600
    minimumHeight: 440
    title: "Nova"
    color: Theme.bg

    property bool streaming: false
    property bool reduceMotion: false
    property bool ollamaOk: false
    property int modelCount: 0
    property string errorText: ""
    property int tokensPerSec: 0
    property bool inspectorRequested: false

    onInspectorRequestedChanged: {
        if (inspectorRequested)
            sidePanel.visible = true;
    }

    // ---- Reusable components -----------------------------------------

    component FlatBtn: Button {
        flat: true
        implicitHeight: 30
        padding: 10
        contentItem: Label {
            text: parent.text
            color: parent.enabled ? Theme.subtext : Theme.disabled
            font.pointSize: Theme.fontSmall
            font.bold: parent.highlighted
        }
        background: Rectangle {
            color: parent.highlighted ? Theme.surfaceAlt : parent.hovered ? Theme.surfaceAlt : "transparent"
            radius: Theme.radiusControl
            border.width: parent.activeFocus ? 2 : 0
            border.color: Theme.accent
        }
        Accessible.name: text
    }

    component IconBtn: Button {
        flat: true
        implicitWidth: 30
        implicitHeight: 30
        contentItem: Label {
            text: parent.text
            color: parent.highlighted ? Theme.accent : parent.enabled ? Theme.subtext : Theme.disabled
            font.pixelSize: 15
        }
        background: Rectangle {
            color: parent.hovered ? Theme.surfaceAlt : "transparent"
            radius: Theme.radiusControl
            border.width: parent.activeFocus ? 2 : 0
            border.color: Theme.accent
        }
    }

    component SectionLabel: Label {
        color: Theme.meta
        font.pointSize: Theme.fontMicro
        font.bold: true
        font.letterSpacing: 1.2
    }

    component FieldBg: Rectangle {
        color: Theme.surfaceAlt
        radius: Theme.radiusControl
        border.color: fieldActive ? Theme.accent : Theme.border
        border.width: 1
        property bool fieldActive: false
    }

    // ---- Model --------------------------------------------------------

    ListModel {
        id: chatModel
    }

    Component.onCompleted: {
        chatBridge.listModels();
        try {
            reduceMotion = JSON.parse(chatBridge.getSettings()).reduce_motion === "true";
        } catch (e) { }
    }

    onClosing: (close) => {
        if (!chatBridge.allowClose()) {
            close.accepted = false;
            visible = false; // hide to tray
        }
    }

    // ---- Bridge wiring -------------------------------------------------

    Connections {
        target: chatBridge

        function onMessageAdded(role, content, isStreaming) {
            if (role === "regenerate") {
                if (chatModel.count > 0)
                    chatModel.remove(chatModel.count - 1);
                return;
            }
            chatModel.append({
                "role": role,
                "content": content,
                "isStreaming": isStreaming,
                "tools": "[]"
            });
            if (chatView.atYEnd)
                chatView.positionViewAtEnd();
        }

        function onStreamUpdated(token) {
            if (chatModel.count > 0) {
                let lastIndex = chatModel.count - 1;
                let item = chatModel.get(lastIndex);
                if (item.isStreaming)
                    chatModel.setProperty(lastIndex, "content", item.content + token);
            }
            tokenCounter.tick();
            if (chatView.atYEnd)
                chatView.positionViewAtEnd();
        }

        function onStreamCompleted() {
            if (chatModel.count > 0) {
                let lastIndex = chatModel.count - 1;
                chatModel.setProperty(lastIndex, "isStreaming", false);
                chatModel.setProperty(lastIndex, "tools", "[]");
            }
            if (chatView.atYEnd)
                chatView.positionViewAtEnd();
        }

        function onStreamingChanged(value) {
            root.streaming = value;
            if (value) {
                input.focus = true;
                tokenCounter.reset();
            }
        }

        function onStatusChanged(status) {
            if (status.startsWith("Error")) {
                root.errorText = status;
                statusLabel.text = "";
            } else {
                statusLabel.text = status;
                statusTimer.restart();
            }
        }

        function onPermissionRequested(toolName, risk, permission, args) {
            permissionDialog.toolName = toolName;
            permissionDialog.risk = risk;
            permissionDialog.permission = permission;
            permissionDialog.arguments = args;
            permissionDialog.open();
        }

        function onModelsListed(models) {
            modelCombo.model = models;
            root.modelCount = models.length;
            root.ollamaOk = true;
            let current = chatBridge.currentModel();
            let idx = models.indexOf(current);
            modelCombo.currentIndex = idx >= 0 ? idx : 0;
        }

        function onModelInfoChanged() {
            sidePanel.refreshModel();
        }

        function onMetricsUpdated(json) {
            sidePanel.system = JSON.parse(json);
        }

        function onResponseMetrics(json) {
            sidePanel.response = JSON.parse(json);
        }

        function onToolStarted(name, argsJson) {
            if (chatModel.count === 0)
                return;
            let lastIndex = chatModel.count - 1;
            let item = chatModel.get(lastIndex);
            let tools = JSON.parse(item.tools);
            tools.push({
                "name": name,
                "args": argsJson,
                "status": "running",
                "durationMs": 0,
                "error": ""
            });
            chatModel.setProperty(lastIndex, "tools", JSON.stringify(tools));
            if (chatView.atYEnd)
                chatView.positionViewAtEnd();
        }

        function onToolFinished(name, ok, durationMs, error) {
            if (chatModel.count === 0)
                return;
            let lastIndex = chatModel.count - 1;
            let item = chatModel.get(lastIndex);
            let tools = JSON.parse(item.tools);
            for (let i = tools.length - 1; i >= 0; i--) {
                if (tools[i].name === name && tools[i].status === "running") {
                    tools[i].status = ok ? "ok" : "fail";
                    tools[i].durationMs = durationMs;
                    tools[i].error = error;
                    break;
                }
            }
            chatModel.setProperty(lastIndex, "tools", JSON.stringify(tools));
            if (chatView.atYEnd)
                chatView.positionViewAtEnd();
        }
    }

    // ---- Permission dialog ---------------------------------------------

    Dialog {
        id: permissionDialog
        modal: true
        anchors.centerIn: parent
        width: 540
        title: "Tool permission required"
        standardButtons: Dialog.NoButton

        property string toolName: ""
        property string risk: ""
        property string permission: ""
        property string arguments: ""
        property color riskColor: risk === "destructive" ? Theme.danger
                               : risk === "write" ? Theme.warn
                               : Theme.ok
        property string riskLabel: risk === "destructive" ? "DESTRUCTIVE — irreversible action"
                                 : risk === "write" ? "WRITE — modifies your system"
                                 : "READ — safe to run"

        ColumnLayout {
            width: parent.width
            spacing: 12

            Rectangle {
                Layout.fillWidth: true
                color: permissionDialog.riskColor
                radius: Theme.radiusControl
                implicitHeight: 30

                Label {
                    anchors.centerIn: parent
                    text: permissionDialog.riskLabel.toUpperCase()
                    color: Theme.accentText
                    font.pointSize: Theme.fontMicro
                    font.bold: true
                    font.letterSpacing: 1
                }
            }

            Label {
                text: "Nova wants to run this tool:"
                color: Theme.subtext
                font.pointSize: Theme.fontSmall
            }

            Label {
                text: permissionDialog.toolName
                font.pointSize: Theme.fontTitle
                font.bold: true
                color: Theme.accent
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                text: "Permission: " + permissionDialog.permission
                color: Theme.meta
                font.pointSize: Theme.fontSmall
                visible: permissionDialog.permission !== ""
            }

            Label {
                text: "Arguments:"
                color: Theme.subtext
                font.pointSize: Theme.fontSmall
                visible: permissionDialog.arguments !== ""
            }

            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(180, argsArea.implicitHeight + 16)
                color: Theme.surface
                radius: Theme.radiusControl
                visible: permissionDialog.arguments !== ""

                ScrollView {
                    anchors.fill: parent
                    anchors.margins: 8
                    clip: true

                    TextArea {
                        id: argsArea
                        readOnly: true
                        text: {
                            try {
                                return JSON.stringify(JSON.parse(permissionDialog.arguments), null, 2);
                            } catch (e) {
                                return permissionDialog.arguments;
                            }
                        }
                        color: Theme.warn
                        font.family: Theme.fontMonoFamily
                        font.pointSize: Theme.fontMono
                        wrapMode: Text.NoWrap
                        background: Rectangle { color: "transparent" }
                    }
                }
            }

            CheckBox {
                id: rememberCheck
                text: "Always allow this tool"
                font.pointSize: Theme.fontSmall
                indicator: Rectangle {
                    implicitWidth: 16
                    implicitHeight: 16
                    radius: 4
                    color: rememberCheck.checked ? Theme.accent : Theme.surfaceAlt
                    border.color: Theme.border
                    Rectangle {
                        anchors.centerIn: parent
                        width: 8
                        height: 8
                        radius: 2
                        color: Theme.accentText
                        visible: rememberCheck.checked
                    }
                }
            }

            RowLayout {
                Layout.alignment: Qt.AlignRight
                spacing: 8

                Button {
                    text: "Deny"
                    implicitHeight: 32
                    padding: 14
                    contentItem: Label {
                        text: parent.text
                        color: Theme.text
                        font.pointSize: Theme.fontSmall
                    }
                    background: Rectangle {
                        color: parent.hovered ? Theme.surfaceAlt : Theme.surface
                        radius: Theme.radiusControl
                        border.color: Theme.border
                        border.width: 1
                    }
                    onClicked: {
                        chatBridge.respondPermission(false, rememberCheck.checked);
                        permissionDialog.close();
                    }
                }

                Button {
                    text: "Approve"
                    implicitHeight: 32
                    padding: 14
                    contentItem: Label {
                        text: parent.text
                        color: Theme.accentText
                        font.pointSize: Theme.fontSmall
                        font.bold: true
                    }
                    background: Rectangle {
                        color: parent.hovered ? Qt.lighter(Theme.accent, 1.08) : Theme.accent
                        radius: Theme.radiusControl
                    }
                    onClicked: {
                        chatBridge.respondPermission(true, rememberCheck.checked);
                        permissionDialog.close();
                    }
                }
            }
        }
    }

    // ---- Settings dialog -----------------------------------------------

    Dialog {
        id: settingsDialog
        modal: true
        anchors.centerIn: parent
        width: 560
        title: "Settings"
        standardButtons: Dialog.Save | Dialog.Cancel

        property var settings: ({})

        function load() {
            settings = JSON.parse(chatBridge.getSettings());
            closeToTraySwitch.checked = settings.close_to_tray === "true";
            autoApproveSwitch.checked = settings.auto_approve_writes === "true";
            alwaysOnTopSwitch.checked = settings.always_on_top === "true";
            reduceMotionSwitch.checked = settings.reduce_motion === "true";
            temperatureSlider.value = parseFloat(settings.temperature);
            ctxCombo.currentIndex = Math.max(0, ctxCombo.model.indexOf(settings.num_ctx));
            urlField.text = settings.ollama_url;
            promptArea.text = settings.system_prompt;
        }

        onOpened: load()

        onAccepted: {
            settings.temperature = temperatureSlider.value.toFixed(2);
            settings.num_ctx = ctxCombo.currentText;
            settings.ollama_url = urlField.text.trim();
            settings.system_prompt = promptArea.text;
            chatBridge.saveSettings(JSON.stringify(settings));
            if (settings.always_on_top === "true")
                root.flags = root.flags | Qt.WindowStaysOnTopHint;
            else
                root.flags = root.flags & ~Qt.WindowStaysOnTopHint;
            root.reduceMotion = settings.reduce_motion === "true";
            statusLabel.text = "Settings saved";
            statusTimer.restart();
        }

        onRejected: load()

        ColumnLayout {
            width: parent.width
            spacing: 14

            TabBar {
                id: settingsTabs
                Layout.fillWidth: true

                background: Rectangle {
                    color: Theme.surface
                    radius: Theme.radiusControl
                }

                TabButton {
                    text: "General"
                    font.pointSize: Theme.fontSmall
                    background: Rectangle {
                        color: settingsTabs.currentIndex === 0 ? Theme.surfaceAlt : "transparent"
                        radius: Theme.radiusControl - 2
                    }
                }
                TabButton {
                    text: "LLM"
                    font.pointSize: Theme.fontSmall
                    background: Rectangle {
                        color: settingsTabs.currentIndex === 1 ? Theme.surfaceAlt : "transparent"
                        radius: Theme.radiusControl - 2
                    }
                }
            }

            StackLayout {
                Layout.fillWidth: true
                currentIndex: settingsTabs.currentIndex

                // ---- General
                ColumnLayout {
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Label {
                                text: "Close to tray"
                                color: Theme.text
                                font.pointSize: Theme.fontBody
                            }
                            Label {
                                text: "Closing the window hides Nova instead of quitting"
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }

                        Switch { id: closeToTraySwitch; onToggled: settings.close_to_tray = checked ? "true" : "false" }
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Label {
                                text: "Auto-approve WRITE tools"
                                color: Theme.text
                                font.pointSize: Theme.fontBody
                            }
                            Label {
                                text: "File/system modifications run without asking (READ tools always run)"
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }

                        Switch { id: autoApproveSwitch; onToggled: settings.auto_approve_writes = checked ? "true" : "false" }
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Label {
                                text: "Always on top"
                                color: Theme.text
                                font.pointSize: Theme.fontBody
                            }
                            Label {
                                text: "Keep the Nova window above other windows"
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }

                        Switch { id: alwaysOnTopSwitch; onToggled: settings.always_on_top = checked ? "true" : "false" }
                    }

                    RowLayout {
                        Layout.fillWidth: true

                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 2

                            Label {
                                text: "Reduce motion"
                                color: Theme.text
                                font.pointSize: Theme.fontBody
                            }
                            Label {
                                text: "Disable animations and transitions"
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                            }
                        }

                        Switch { id: reduceMotionSwitch; onToggled: settings.reduce_motion = checked ? "true" : "false" }
                    }
                }

                // ---- LLM
                ColumnLayout {
                    spacing: 14

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        RowLayout {
                            Layout.fillWidth: true

                            Label {
                                text: "Temperature"
                                color: Theme.text
                                font.pointSize: Theme.fontBody
                            }

                            Label {
                                text: temperatureSlider.value.toFixed(2)
                                color: Theme.accent
                                font.bold: true
                                font.pointSize: Theme.fontBody
                            }
                        }

                        Slider {
                            id: temperatureSlider
                            Layout.fillWidth: true
                            from: 0.0
                            to: 1.5
                            stepSize: 0.05
                            snapMode: Slider.SnapOnRelease
                            onMoved: settings.temperature = value.toFixed(2)
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Label {
                            text: "Context size (num_ctx)"
                            color: Theme.text
                            font.pointSize: Theme.fontBody
                        }

                        ComboBox {
                            id: ctxCombo
                            Layout.fillWidth: true
                            model: ["2048", "4096", "8192", "16384", "32768", "65536"]
                            onActivated: settings.num_ctx = currentText
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Label {
                            text: "Ollama URL (applies on next start)"
                            color: Theme.text
                            font.pointSize: Theme.fontBody
                        }

                        TextField {
                            id: urlField
                            Layout.fillWidth: true
                            color: Theme.text
                            placeholderText: "http://localhost:11434"
                            placeholderTextColor: Theme.faint
                            background: FieldBg { fieldActive: urlField.activeFocus }
                        }
                    }

                    ColumnLayout {
                        Layout.fillWidth: true
                        spacing: 4

                        Label {
                            text: "System prompt"
                            color: Theme.text
                            font.pointSize: Theme.fontBody
                        }

                        TextArea {
                            id: promptArea
                            Layout.fillWidth: true
                            Layout.preferredHeight: 110
                            color: Theme.text
                            placeholderText: "Default: You are Nova, a helpful local AI desktop assistant..."
                            placeholderTextColor: Theme.faint
                            wrapMode: Text.Wrap
                            background: FieldBg { fieldActive: promptArea.activeFocus }
                        }
                    }
                }
            }
        }
    }

    // ---- Skills dialog ---------------------------------------------------

    Dialog {
        id: skillsDialog
        modal: true
        anchors.centerIn: parent
        width: 580
        height: 500
        title: "Skills"
        standardButtons: Dialog.Close

        property var skills: []

        onOpened: skills = JSON.parse(chatBridge.skills())

        ColumnLayout {
            anchors.fill: parent
            spacing: 12

            Label {
                text: "Skills add tools Nova can use in conversation. Enable a skill and the assistant can call its tools automatically when relevant — the usual permission rules still apply."
                color: Theme.subtext
                font.pointSize: Theme.fontSmall
                wrapMode: Text.Wrap
                Layout.fillWidth: true
            }

            ListView {
                id: skillsList
                Layout.fillWidth: true
                Layout.fillHeight: true
                spacing: 10
                clip: true

                model: skillsDialog.skills

                delegate: Rectangle {
                    width: skillsList.width
                    color: Theme.surface
                    radius: Theme.radiusCard
                    border.color: Theme.border
                    border.width: 1
                    implicitHeight: skillsColumn.implicitHeight + Theme.s4

                    ColumnLayout {
                        id: skillsColumn
                        anchors.fill: parent
                        anchors.margins: Theme.s3
                        spacing: Theme.s1

                        RowLayout {
                            Layout.fillWidth: true

                            Label {
                                text: modelData.name
                                color: Theme.accent
                                font.bold: true
                                font.pointSize: Theme.fontTitle
                                Layout.fillWidth: true
                                elide: Text.ElideRight
                            }

                            Switch {
                                checked: modelData.enabled
                                onToggled: {
                                    chatBridge.toggleSkill(modelData.name, checked);
                                    modelData.enabled = checked;
                                }
                            }
                        }

                        Label {
                            text: modelData.description
                            color: Theme.text
                            font.pointSize: Theme.fontSmall
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }

                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            visible: modelData.tools.length > 0

                            Repeater {
                                model: modelData.tools

                                delegate: Rectangle {
                                    color: Theme.surfaceAlt
                                    radius: Theme.radiusControl
                                    implicitWidth: toolRow.implicitWidth + 14
                                    implicitHeight: 22

                                    RowLayout {
                                        id: toolRow
                                        anchors.fill: parent
                                        anchors.leftMargin: 7
                                        anchors.rightMargin: 7
                                        spacing: 5

                                        Rectangle {
                                            width: 8
                                            height: 8
                                            radius: 4
                                            color: modelData.risk === "read" ? Theme.ok
                                                 : modelData.risk === "write" ? Theme.warn
                                                 : Theme.danger
                                        }

                                        Label {
                                            text: modelData.name
                                            color: Theme.subtext
                                            font.pointSize: Theme.fontMicro
                                        }
                                    }
                                }
                            }
                        }

                        Label {
                            text: "No tools"
                            color: Theme.faint
                            font.pointSize: Theme.fontSmall
                            visible: modelData.tools.length === 0
                        }
                    }
                }
            }
        }
    }

    Timer {
        id: statusTimer
        interval: 5000
        onTriggered: statusLabel.text = ""
    }

    // ---- Main layout -------------------------------------------------------

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.s4
        spacing: Theme.s3

        // Header
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            ColumnLayout {
                spacing: 0

                Label {
                    text: "Nova"
                    font.pointSize: Theme.fontDisplay
                    font.bold: true
                    color: Theme.text
                }

                Label {
                    text: "local AI assistant"
                    color: Theme.meta
                    font.pointSize: Theme.fontMicro
                    font.letterSpacing: 1
                }
            }

            Item { Layout.fillWidth: true }

            FlatBtn {
                text: "Clear"
                highlighted: true
                visible: chatModel.count > 0
                ToolTip.visible: hovered
                ToolTip.text: "Start a new conversation (Ctrl+N)"
                onClicked: {
                    chatModel.clear();
                    chatBridge.clearHistory();
                }
            }

            IconBtn {
                text: "\u21BB"
                enabled: !root.streaming && chatModel.count > 1
                ToolTip.visible: hovered
                ToolTip.text: "Regenerate last answer"
                Accessible.name: "Regenerate last answer"
                onClicked: chatBridge.regenerateLast()
            }

            IconBtn {
                text: "\u2699"
                ToolTip.visible: hovered
                ToolTip.text: "Settings (Ctrl+,)"
                Accessible.name: "Settings"
                onClicked: settingsDialog.open()
            }

            IconBtn {
                text: "\u2630"
                highlighted: sidePanel.visible
                ToolTip.visible: hovered
                ToolTip.text: "Toggle system inspector"
                Accessible.name: "Toggle system inspector"
                onClicked: sidePanel.visible = !sidePanel.visible
            }

            ComboBox {
                id: modelCombo
                width: 180
                font.pointSize: Theme.fontSmall
                onActivated: (index) => {
                    chatBridge.selectModel(currentText);
                }
            }
        }

        // Chat + inspector
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.s3

            // ---- Chat card
            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                color: Theme.surface
                radius: Theme.radiusCard
                border.color: Theme.border
                border.width: 1

                ListView {
                    id: chatView
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    spacing: Theme.s3
                    model: chatModel
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    add: Transition {
                        enabled: !root.reduceMotion
                        NumberAnimation {
                            properties: "opacity, y"
                            from: 0; to: 1
                            duration: Theme.durationFast
                            easing.type: Easing.OutCubic
                        }
                    }
                    remove: Transition {
                        enabled: !root.reduceMotion
                        NumberAnimation {
                            properties: "opacity, y"
                            from: 1; to: 0
                            duration: Theme.durationFast
                            easing.type: Easing.OutCubic
                        }
                    }

                    // Empty state
                    Item {
                        visible: chatModel.count === 0
                        anchors.fill: parent

                        ColumnLayout {
                            anchors.centerIn: parent
                            width: Math.min(parent.width - 80, 420)
                            spacing: Theme.s3

                            Label {
                                text: "Nova"
                                color: Theme.text
                                font.pointSize: 21
                                font.bold: true
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }

                            Label {
                                text: "Your local AI assistant. Runs on your hardware, stays on your machine."
                                color: Theme.subtext
                                font.pointSize: Theme.fontBody
                                wrapMode: Text.Wrap
                                horizontalAlignment: Text.AlignHCenter
                                Layout.fillWidth: true
                            }

                            RowLayout {
                                Layout.alignment: Qt.AlignHCenter
                                spacing: 6

                                Rectangle {
                                    width: 8
                                    height: 8
                                    radius: 4
                                    color: root.ollamaOk ? Theme.ok : Theme.danger
                                }

                                Label {
                                    text: root.ollamaOk
                                          ? "Ollama connected" + (root.modelCount > 0 ? " \u00B7 " + root.modelCount + (root.modelCount === 1 ? " model" : " models") : "")
                                          : "Ollama not reachable — start the service, then restart Nova"
                                    color: Theme.subtext
                                    font.pointSize: Theme.fontSmall
                                }
                            }

                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: Theme.s2
                                Layout.topMargin: Theme.s2

                                FlatBtn {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 36
                                    text: "Generate a system report"
                                    onClicked: chatBridge.sendMessage("Generate a system report with the latest stats")
                                }
                                FlatBtn {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 36
                                    text: "What can you do?"
                                    onClicked: chatBridge.sendMessage("What can you do?")
                                }
                                FlatBtn {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 36
                                    text: "Check GPU and memory usage"
                                    onClicked: chatBridge.sendMessage("How much GPU and memory is being used right now?")
                                }
                            }
                        }
                    }

                    delegate: Item {
                        width: chatView.width - Theme.s5
                        height: messageRow.height

                        property var toolsParsed: JSON.parse(model.tools)

                        RowLayout {
                            id: messageRow
                            anchors.left: parent.left
                            anchors.right: parent.right
                            height: messageCol.height
                            layoutDirection: model.role === "user" ? Qt.RightToLeft : Qt.LeftToRight

                            ColumnLayout {
                                id: messageCol
                                spacing: 3
                                Layout.alignment: Qt.AlignBottom

                                Label {
                                    text: model.role === "user" ? "You" : "Nova"
                                    color: Theme.meta
                                    font.pointSize: Theme.fontMicro
                                    font.bold: true
                                    font.letterSpacing: 0.8
                                    horizontalAlignment: model.role === "user" ? Text.AlignRight : Text.AlignLeft
                                    Layout.alignment: model.role === "user" ? Qt.AlignRight : Qt.AlignLeft
                                    visible: model.content !== "" || model.isStreaming
                                }

                                RowLayout {
                                    spacing: Theme.s2

                                    // Bubble
                                    Rectangle {
                                        id: bubble
                                        Layout.maximumWidth: chatView.width * 0.72
                                        implicitWidth: Math.min(
                                            chatView.width * 0.72,
                                            Math.max(textRow.implicitWidth, toolsColumn.implicitWidth) + Theme.s5
                                        )
                                        implicitHeight: textRow.height + toolsColumn.height + Theme.s4
                                        radius: Theme.radiusBubble
                                        color: model.role === "user" ? Theme.userBubble : Theme.assistantBubble

                                        ColumnLayout {
                                            anchors.fill: parent
                                            anchors.margins: Theme.s2
                                            spacing: Theme.s2

                                            RowLayout {
                                                id: textRow
                                                Layout.fillWidth: true
                                                spacing: 4

                                                Label {
                                                    id: textMessage
                                                    text: model.content
                                                    color: model.role === "user" ? Theme.userText : Theme.assistantText
                                                    wrapMode: Text.Wrap
                                                    font.pointSize: Theme.fontBody
                                                    textFormat: Text.MarkdownText
                                                    linkColor: model.role === "user" ? Theme.userText : Theme.accent
                                                    Layout.fillWidth: true
                                                    width: undefined
                                                }

                                                // Streaming caret
                                                Rectangle {
                                                    width: 2
                                                    height: Theme.fontBody * 1.4
                                                    radius: 1
                                                    color: model.role === "user" ? Theme.userText : Theme.accent
                                                    visible: model.isStreaming && caretTimer.running
                                                    opacity: caretTimer.opacity
                                                }
                                            }

// Tool activity timeline
                                    ColumnLayout {
                                        id: toolsColumn
                                        Layout.fillWidth: true
                                        spacing: 4
                                        visible: toolsParsed.length > 0

                                        Repeater {
                                            model: toolsParsed

                                                    delegate: ColumnLayout {
                                                        Layout.fillWidth: true
                                                        spacing: 2
                                                        property bool expanded: false

                                                        RowLayout {
                                                            Layout.fillWidth: true
                                                            spacing: 6

                                                            Rectangle {
                                                                width: 7
                                                                height: 7
                                                                radius: 4
                                                                color: modelData.status === "running" ? Theme.accent
                                                                     : modelData.status === "ok" ? Theme.ok
                                                                     : Theme.danger
                                                            }

                                                            Label {
                                                                text: modelData.status === "running" ? "\u25C6 " + modelData.name
                                                                     : modelData.status === "ok" ? "\u2713 " + modelData.name
                                                                     : "\u2717 " + modelData.name
                                                                color: modelData.status === "fail" ? Theme.danger : Theme.subtext
                                                                font.family: Theme.fontMonoFamily
                                                                font.pointSize: Theme.fontMono
                                                                Layout.fillWidth: true
                                                                elide: Text.ElideRight
                                                            }

                                                            Label {
                                                                text: modelData.status === "running"
                                                                      ? "running\u2026"
                                                                      : modelData.durationMs > 0 ? modelData.durationMs + " ms" : ""
                                                                color: Theme.meta
                                                                font.pointSize: Theme.fontMicro
                                                            }

                                                            MouseArea {
                                                                implicitWidth: 18
                                                                implicitHeight: 18
                                                                enabled: modelData.args !== "" && modelData.args !== "{}"
                                                                onClicked: expanded = !expanded

                                                                Label {
                                                                    anchors.centerIn: parent
                                                                    text: parent.enabled ? (expanded ? "\u25B4" : "\u25BE") : ""
                                                                    color: Theme.meta
                                                                    font.pointSize: Theme.fontMicro
                                                                }
                                                            }
                                                        }

                                                        Label {
                                                            Layout.fillWidth: true
                                                            visible: expanded
                                                            text: {
                                                                try {
                                                                    return JSON.stringify(JSON.parse(modelData.args), null, 2);
                                                                } catch (e) {
                                                                    return modelData.args;
                                                                }
                                                            }
                                                            color: Theme.warn
                                                            font.family: Theme.fontMonoFamily
                                                            font.pointSize: Theme.fontMono
                                                            wrapMode: Text.Wrap
                                                            Layout.leftMargin: 12
                                                        }

                                                        Label {
                                                            Layout.fillWidth: true
                                                            visible: modelData.status === "fail" && modelData.error !== ""
                                                            text: modelData.error
                                                            color: Theme.danger
                                                            font.pointSize: Theme.fontMicro
                                                            wrapMode: Text.Wrap
                                                        }
                                                    }
                                                }
                                            }
                                        }
                                    }

                                    // Copy (hover-reveal, assistant only)
                                    Button {
                                        text: "Copy"
                                        flat: true
                                        visible: model.role === "assistant" && model.content !== ""
                                        opacity: copyHover.containsMouse || mouseInsideMessage.containsMouse ? 1 : 0
                                        Behavior on opacity { enabled: !root.reduceMotion; NumberAnimation { duration: Theme.durationFast } }
                                        implicitHeight: 28
                                        contentItem: Label {
                                            text: parent.text
                                            color: Theme.accent
                                            font.pointSize: Theme.fontMicro
                                        }
                                        background: Rectangle {
                                            color: parent.hovered ? Theme.surfaceAlt : "transparent"
                                            radius: Theme.radiusControl
                                        }
                                        onClicked: {
                                            chatBridge.copyToClipboard(model.content);
                                            statusLabel.text = "Copied";
                                            statusTimer.restart();
                                        }
                                    }
                                }
                            }

                            // Spacer
                            Item {
                                Layout.fillWidth: true
                            }
                        }

                        HoverHandler {
                            id: mouseInsideMessage
                            acceptedDevices: PointerDevice.Mouse
                        }

                        Timer {
                            id: caretTimer
                            interval: 550
                            running: model.isStreaming
                            repeat: true
                            property real opacity: 1.0
                            onTriggered: opacity = opacity === 1.0 ? 0.0 : 1.0
                        }
                    }
                }
            }

            // ---- Inspector (right side panel)
            Rectangle {
                id: sidePanel
                Layout.fillHeight: true
                Layout.preferredWidth: 250
                visible: false
                color: Theme.surface
                radius: Theme.radiusCard
                border.color: Theme.border
                border.width: 1

                Behavior on visible {
                    enabled: !root.reduceMotion
                    NumberAnimation { duration: Theme.durationMedium; easing.type: Easing.OutCubic }
                }

                property var model: ({})
                property var response: ({})
                property var system: ({})

                function refreshModel() {
                    let raw = chatBridge.modelInfo();
                    try { model = JSON.parse(raw); } catch (e) { model = {}; }
                }

                function modelMeta() {
                    let m = model;
                    let parts = [];
                    if (m.parameter_size) parts.push(m.parameter_size);
                    if (m.quantization) parts.push(m.quantization);
                    if (m.context_length) parts.push(m.context_length + " ctx");
                    return parts.join(" \u00B7 ") || "\u2014";
                }

                function fmtMiB(n) {
                    return (n === undefined || n === null) ? "\u2014" : n + " MiB";
                }

                function fmtGB(bytes) {
                    if (bytes === undefined || bytes === null) return "\u2014";
                    return (bytes / 1e9).toFixed(1) + " GB";
                }

                function tokPerSec() {
                    let r = response;
                    if (r.tokens && r.eval_ns && r.eval_ns > 0)
                        return (r.tokens / (r.eval_ns / 1e9)).toFixed(1);
                    return "\u2014";
                }

                function pct(used, total) {
                    if (!used || !total) return 0;
                    return Math.min(1, used / total);
                }

                Component.onCompleted: refreshModel()
                onVisibleChanged: {
                    if (visible) refreshModel();
                }

                ColumnLayout {
                    anchors.fill: parent
                    anchors.margins: Theme.s3
                    spacing: Theme.s3

                    // Model card
                    Rectangle {
                        Layout.fillWidth: true
                        color: Theme.bg
                        radius: Theme.radiusCard - 2
                        implicitHeight: modelCardColumn.implicitHeight + Theme.s4

                        ColumnLayout {
                            id: modelCardColumn
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: 3

                            SectionLabel { text: "MODEL" }
                            Label {
                                text: sidePanel.model.name || "\u2014"
                                color: Theme.accent
                                font.pointSize: Theme.fontTitle
                                font.bold: true
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: sidePanel.modelMeta()
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                        }
                    }

                    // Last response card
                    Rectangle {
                        Layout.fillWidth: true
                        color: Theme.bg
                        radius: Theme.radiusCard - 2
                        implicitHeight: responseCardColumn.implicitHeight + Theme.s4

                        ColumnLayout {
                            id: responseCardColumn
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: 3

                            SectionLabel { text: "LAST RESPONSE" }
                            Label {
                                text: sidePanel.response.tokens
                                      ? sidePanel.response.tokens + " tokens \u00B7 " + sidePanel.tokPerSec() + " tok/s"
                                      : "No response yet"
                                color: Theme.text
                                font.pointSize: Theme.fontSmall
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Label {
                                text: sidePanel.response.prompt_tokens
                                      ? "Prompt: " + sidePanel.response.prompt_tokens + " tokens"
                                      : ""
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                visible: text !== ""
                            }
                            Label {
                                text: sidePanel.response.tools
                                      ? "Tools used: " + sidePanel.response.tools
                                      : ""
                                color: Theme.warn
                                font.pointSize: Theme.fontSmall
                                visible: text !== ""
                            }
                            Label {
                                text: sidePanel.response.model
                                      ? "Model: " + sidePanel.response.model
                                      : ""
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                                visible: text !== ""
                            }
                        }
                    }

                    // System card
                    Rectangle {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        color: Theme.bg
                        radius: Theme.radiusCard - 2
                        implicitHeight: 200

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: Theme.s3
                            spacing: 6

                            SectionLabel { text: "SYSTEM" }

                            Label {
                                text: sidePanel.system.gpu && sidePanel.system.gpu.name
                                      ? sidePanel.system.gpu.name
                                      : "GPU \u2014"
                                color: Theme.text
                                font.pointSize: Theme.fontSmall
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Label {
                                    text: sidePanel.system.gpu
                                          ? sidePanel.fmtMiB(sidePanel.system.gpu.vram_used_mib) + " / " + sidePanel.fmtMiB(sidePanel.system.gpu.vram_total_mib)
                                          : "VRAM \u2014"
                                    color: Theme.subtext
                                    font.pointSize: Theme.fontSmall
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 6
                                    radius: 3
                                    color: Theme.surfaceAlt

                                    Rectangle {
                                        width: parent.width * sidePanel.pct(
                                            sidePanel.system.gpu ? sidePanel.system.gpu.vram_used_mib : 0,
                                            sidePanel.system.gpu ? sidePanel.system.gpu.vram_total_mib : 0)
                                        height: parent.height
                                        radius: 3
                                        color: Theme.accent
                                    }
                                }
                            }

                            Label {
                                text: sidePanel.system.gpu && sidePanel.system.gpu.temperature_c
                                      ? "GPU " + sidePanel.system.gpu.temperature_c + " \u00B0C \u00B7 " +
                                        (sidePanel.system.gpu.utilization_pct !== undefined
                                         ? sidePanel.system.gpu.utilization_pct + "% util"
                                         : "")
                                      : ""
                                color: sidePanel.system.gpu && sidePanel.system.gpu.temperature_c > 80
                                       ? Theme.danger : Theme.subtext
                                font.pointSize: Theme.fontSmall
                                visible: text !== ""
                            }

                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 6

                                Label {
                                    text: sidePanel.system.memory
                                          ? "RAM " + sidePanel.fmtGB(sidePanel.system.memory.used_bytes) + " / " + sidePanel.fmtGB(sidePanel.system.memory.total_bytes)
                                          : "RAM \u2014"
                                    color: Theme.subtext
                                    font.pointSize: Theme.fontSmall
                                }

                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.preferredHeight: 6
                                    radius: 3
                                    color: Theme.surfaceAlt

                                    Rectangle {
                                        width: parent.width * sidePanel.pct(
                                            sidePanel.system.memory ? sidePanel.system.memory.used_bytes : 0,
                                            sidePanel.system.memory ? sidePanel.system.memory.total_bytes : 0)
                                        height: parent.height
                                        radius: 3
                                        color: Theme.ok
                                    }
                                }
                            }

                            Label {
                                text: sidePanel.system.cpu && sidePanel.system.cpu.cores
                                      ? "CPU " + sidePanel.system.cpu.cores + " cores"
                                      : "CPU \u2014"
                                color: Theme.subtext
                                font.pointSize: Theme.fontSmall
                            }
                        }
                    }
                }
            }
        }

        // ---- Error banner
        Rectangle {
            Layout.fillWidth: true
            visible: root.errorText !== ""
            color: Qt.rgba(0.95, 0.55, 0.66, 0.12)
            radius: Theme.radiusControl
            implicitHeight: errorRow.implicitHeight + Theme.s3

            RowLayout {
                id: errorRow
                anchors.fill: parent
                anchors.margins: Theme.s2
                spacing: Theme.s2

                Label {
                    text: "\u26A0"
                    color: Theme.danger
                    font.pointSize: Theme.fontBody
                }

                Label {
                    text: root.errorText
                    color: Theme.danger
                    font.pointSize: Theme.fontSmall
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }

                FlatBtn {
                    text: "Retry"
                    visible: chatModel.count > 1
                    onClicked: {
                        root.errorText = "";
                        chatBridge.regenerateLast();
                    }
                }

                IconBtn {
                    text: "\u2715"
                    ToolTip.text: "Dismiss"
                    Accessible.name: "Dismiss error"
                    onClicked: root.errorText = ""
                }
            }
        }

        // ---- Status strip
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            Label {
                id: statusLabel
                text: ""
                color: Theme.subtext
                font.pointSize: Theme.fontSmall
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            Label {
                text: root.streaming ? root.tokensPerSec + " tok/s" : ""
                color: Theme.accent
                font.family: Theme.fontMonoFamily
                font.pointSize: Theme.fontMono
            }

            Label {
                text: sidePanel.system.gpu && sidePanel.system.gpu.temperature_c
                      ? "GPU " + sidePanel.system.gpu.temperature_c + "\u00B0C"
                      : ""
                color: Theme.meta
                font.pointSize: Theme.fontMicro
            }
        }

        // ---- Input row
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.s2

            TextArea {
                id: input
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(Math.max(implicitHeight, 40), 120)
                placeholderText: "Ask Nova anything \u2014 Enter to send, Shift+Enter for a new line"
                placeholderTextColor: Theme.faint
                color: Theme.text
                wrapMode: Text.Wrap
                selectByMouse: true
                font.pointSize: Theme.fontBody
                background: FieldBg { fieldActive: input.activeFocus }

                Keys.onPressed: (event) => {
                    if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                            && !(event.modifiers & Qt.ShiftModifier)
                            && !(event.modifiers & Qt.ControlModifier)) {
                        send();
                        event.accepted = true;
                    }
                }
            }

            Button {
                id: sendButton
                text: root.streaming ? "Stop" : "Send"
                implicitWidth: 84
                implicitHeight: 40
                contentItem: Label {
                    text: parent.text
                    color: root.streaming ? Theme.danger : Theme.accentText
                    font.pointSize: Theme.fontBody
                    font.bold: true
                }
                background: Rectangle {
                    color: root.streaming ? Theme.surfaceAlt : Theme.accent
                    radius: Theme.radiusControl
                    border.color: root.streaming ? Theme.danger : "transparent"
                    border.width: root.streaming ? 1 : 0
                }
                onClicked: {
                    if (root.streaming)
                        chatBridge.stopGeneration();
                    else
                        send();
                }
            }
        }
    }

    // ---- Token rate counter ----------------------------------------------

    Timer {
        id: tokenCounter
        interval: 1000
        repeat: true
        running: root.streaming
        property int tokensInWindow: 0

        function tick() { tokensInWindow++ }
        function reset() {
            tokensInWindow = 0;
            root.tokensPerSec = 0;
            restart();
        }

        onTriggered: {
            root.tokensPerSec = tokensInWindow;
            tokensInWindow = 0;
        }
    }

    // ---- Shortcuts ----------------------------------------------------------

    Shortcut {
        sequence: "Ctrl+Return"
        onActivated: send()
    }
    Shortcut {
        sequence: "Ctrl+Enter"
        onActivated: send()
    }
    Shortcut {
        sequence: "Esc"
        enabled: root.streaming
        onActivated: chatBridge.stopGeneration()
    }
    Shortcut {
        sequence: "Ctrl+N"
        onActivated: {
            chatModel.clear();
            chatBridge.clearHistory();
        }
    }
    Shortcut {
        sequence: "Ctrl+K"
        onActivated: input.forceActiveFocus()
    }
    Shortcut {
        sequence: "Ctrl+,"
        onActivated: settingsDialog.open()
    }

    // ---- Send ---------------------------------------------------------------

    function send() {
        let text = input.text.trim();
        if (text.length === 0 || root.streaming)
            return;
        chatBridge.sendMessage(text);
        input.text = "";
    }
}