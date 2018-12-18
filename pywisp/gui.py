# -*- coding: utf-8 -*-
import os
from copy import deepcopy

import pkg_resources
import serial.tools.list_ports
import yaml
from PyQt5.QtCore import QSize, Qt, pyqtSlot, pyqtSignal, QModelIndex, QRectF, QTimer, QSettings, QCoreApplication
from PyQt5.QtGui import *
from PyQt5.QtWidgets import *
from pyqtgraph import PlotWidget, exporters, TextItem, mkBrush
from pyqtgraph.dockarea import *

from .connection import SerialConnection, TcpConnection
from .experiments import ExperimentInteractor, ExperimentView
from .registry import *
from .utils import get_resource, PlainTextLogger, DataPointBuffer, PlotChart, CSVExporter, DataIntDialog
from .visualization import MplVisualizer


class MainGui(QMainWindow):
    runExp = pyqtSignal()
    stopExp = pyqtSignal()

    def __init__(self, fileName=None, parent=None):
        super(MainGui, self).__init__(parent)

        QCoreApplication.setOrganizationName("IACE")
        QCoreApplication.setOrganizationDomain("https://umit.at/iace")
        QCoreApplication.setApplicationVersion(
            pkg_resources.require("PyWisp")[0].version)
        QCoreApplication.setApplicationName(globals()["__package__"])

        self.connections = {}
        self.isConnected = False

        self.timer = QTimer()
        self.timer.timeout.connect(self.updateDataPlots)

        # initialize logger
        self._logger = logging.getLogger(self.__class__.__name__)

        # load settings
        self._settings = QSettings()
        self._initSettings()

        # create experiment
        self._experiments = []

        # window properties
        icon_size = QSize(25, 25)
        res_path = get_resource("icon.png")
        icon = QIcon(res_path)
        self.setWindowIcon(icon)
        self.resize(1000, 700)
        self.setWindowTitle('Visualization')

        # status bar
        self.statusBar = QStatusBar(self)
        self.setStatusBar(self.statusBar)
        self.statusbarLabel = QLabel("Not connected!")
        self.statusBar.addPermanentWidget(self.statusbarLabel, 1)
        self.coordLabel = QLabel("x=0.0 y=0.0")
        self.statusBar.addPermanentWidget(self.coordLabel)

        # the docking area allows to rearrange the user interface at runtime
        self.area = DockArea()
        self.setCentralWidget(self.area)

        # create docks
        self.experimentDock = Dock("Experiments")
        self.lastMeasDock = Dock("Last Measurements")
        self.propertyDock = Dock("Parameters")
        self.logDock = Dock("Log")
        self.dataDock = Dock("Data")
        self.animationDock = Dock("Animation")

        # arrange docks
        self.area.addDock(self.animationDock, "right")
        self.area.addDock(self.lastMeasDock, "left", self.animationDock)
        self.area.addDock(self.propertyDock, "bottom", self.lastMeasDock)
        self.area.addDock(self.dataDock, "bottom", self.propertyDock)
        self.area.addDock(self.logDock, "bottom", self.dataDock)
        self.area.addDock(self.experimentDock, "left", self.lastMeasDock)
        self.nonPlottingDocks = list(self.area.findAll()[1].keys())

        self.standardDockState = self.area.saveState()

        # property dock
        self.targetView = ExperimentView(self)
        self.targetView.expanded.connect(self.targetViewChanged)
        self.targetView.collapsed.connect(self.targetViewChanged)

        self.propertyDock.addWidget(self.targetView)

        # animation dock
        self.animationWidget = QWidget()
        availableVis = getRegisteredVisualizers()
        self._logger.info("Found Visualization: {}".format([name for cls, name in availableVis]))
        if availableVis:
            # instantiate the first visualizer
            self._logger.info("loading visualizer '{}'".format(availableVis[0][1]))
            self.animationLayout = QVBoxLayout()
            if issubclass(availableVis[0][0], MplVisualizer):
                self.animationWidget = QWidget()
                self.visualizer = availableVis[0][0](self.animationWidget,
                                                     self.animationLayout)
                self.animationDock.addWidget(self.animationWidget)
        else:
            self.visualizer = None
        self.animationDock.addWidget(self.animationWidget)

        # experiment dock
        self.experimentList = QListWidget(self)
        self.experimentList.setSelectionMode(QAbstractItemView.SingleSelection)
        self.experimentDock.addWidget(self.experimentList)
        self.experimentList.itemDoubleClicked.connect(self.experimentDclicked)
        self._currentExperimentIndex = None
        self._currentExperimentName = None
        self._experimentStartTime = 0

        self.actStartExperiment = QAction(self)
        self.actStartExperiment.setDisabled(True)
        self.actStartExperiment.setText("&Start experiment")
        self.actStartExperiment.setIcon(QIcon(get_resource("play.png")))
        self.actStartExperiment.setShortcut(QKeySequence("F5"))
        self.actStartExperiment.triggered.connect(self.startExperiment)

        self.actStopExperiment = QAction(self)
        self.actStopExperiment.setText("&Stop experiment")
        self.actStopExperiment.setDisabled(True)
        self.actStopExperiment.setIcon(QIcon(get_resource("stop.png")))
        self.actStopExperiment.setShortcut(QKeySequence("F6"))
        self.actStopExperiment.triggered.connect(self.stopExperiment)

        # lastmeas dock
        self.lastMeasList = QListWidget(self)
        self.lastMeasDock.addWidget(self.lastMeasList)
        self.lastMeasList.itemDoubleClicked.connect(self.loadLastMeas)
        self.measurements = []

        # log dock
        self.logBox = QTextEdit(self)
        self.logBox.setReadOnly(True)
        self.logBox.setLineWrapMode(QTextEdit.NoWrap)
        self.logBox.ensureCursorVisible()
        self.logDock.addWidget(self.logBox)

        # daten dock
        self.dataWidget = QWidget()
        self.dataLayout = QHBoxLayout()

        self.dataPointListWidget = QListWidget()
        self.dataPointListLayout = QVBoxLayout()
        self.dataPointListWidget.setLayout(self.dataPointListLayout)
        self.dataPointListWidget.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.dataLayout.addWidget(self.dataPointListWidget)

        self.dataPointManipulationWidget = QWidget()
        self.dataPointManipulationLayout = QVBoxLayout()
        self.dataPointManipulationLayout.addStretch()
        self.dataPointManipulationLayout.setSpacing(5)
        self.dataPointRightButtonWidget = QWidget()
        self.dataPointRightButtonLayout = QVBoxLayout()
        self.dataPointRightButton = QPushButton(chr(0x226b), self)
        self.dataPointRightButton.setToolTip(
            "Add the selected data set from the left to the selected plot "
            "on the right.")
        self.dataPointRightButton.clicked.connect(self.addDatapointToTree)
        self.dataPointLabel = QLabel('Data point', self)
        self.dataPointLabel.setAlignment(Qt.AlignCenter)
        self.dataPointManipulationLayout.addWidget(self.dataPointLabel)
        self.dataPointManipulationLayout.addWidget(self.dataPointRightButton)
        self.dataPointLeftButtonWidget = QWidget()
        self.dataPointLeftButtonLayout = QVBoxLayout()
        self.dataPointLeftButton = QPushButton(chr(0x03A7), self)
        self.dataPointLeftButton.setToolTip(
            "Remove the selected data set from the plot on the right."
        )
        self.dataPointLeftButton.clicked.connect(self.removeDatapointFromTree)
        self.dataPointManipulationLayout.addWidget(self.dataPointLeftButton)
        self.dataPointExportButton = QPushButton(chr(0x25BC), self)
        self.dataPointExportButton.setToolTip(
            "Export the selected data set from the left to the selected plot "
            "on the right."
        )
        self.dataPointExportButton.clicked.connect(self.exportDataPointFromTree)
        self.dataPointManipulationLayout.addWidget(self.dataPointExportButton)
        self.dataPointPlotAddButtonWidget = QWidget()
        self.dataPointPlotAddButtonLayout = QVBoxLayout()
        self.dataPointPlotAddButton = QPushButton("+", self)
        self.dataPointPlotAddButton.setToolTip(
            "Create a new plot window."
        )
        self.dataPointPlotAddButton.clicked.connect(self.addPlotTreeItem)
        self.plotLabel = QLabel('Plots', self)
        self.plotLabel.setAlignment(Qt.AlignCenter)
        self.dataPointManipulationLayout.addWidget(self.plotLabel)
        self.dataPointManipulationLayout.addWidget(self.dataPointPlotAddButton)
        self.dataPointPlotRemoveButtonWidget = QWidget()
        self.dataPointPlotRemoveButtonLayout = QVBoxLayout()
        self.dataPointPlotRemoveButton = QPushButton("-", self)
        self.dataPointPlotRemoveButton.setToolTip(
            "Delete the selected plot window."
        )
        self.dataPointPlotRemoveButton.clicked.connect(self.removeSelectedPlotTreeItems)
        self.dataPointManipulationLayout.addWidget(self.dataPointPlotRemoveButton)
        self.dataPointManipulationWidget.setLayout(self.dataPointManipulationLayout)
        self.dataLayout.addWidget(self.dataPointManipulationWidget)

        self.dataPointTreeWidget = QTreeWidget()
        self.dataPointTreeWidget.setHeaderLabels(["Plot title", "Data point"])
        self.dataPointTreeWidget.itemDoubleClicked.connect(self.plotVectorClicked)
        self.dataPointTreeWidget.setExpandsOnDoubleClick(0)
        self.dataPointTreeLayout = QVBoxLayout()

        self.dataPointTreeWidget.setLayout(self.dataPointTreeLayout)
        self.dataLayout.addWidget(self.dataPointTreeWidget)

        self.dataWidget.setLayout(self.dataLayout)
        self.dataDock.addWidget(self.dataWidget)

        # init logger for logging box
        self.textLogger = PlainTextLogger(self._settings,
                                          logging.INFO)
        self.textLogger.set_target_cb(self.logBox)
        logging.getLogger().addHandler(self.textLogger)
        self._logger.info('Laboratory visualization')

        # menu bar
        dateiMenu = self.menuBar().addMenu("&File")
        dateiMenu.addAction("&Quit", self.close, QKeySequence(Qt.CTRL + Qt.Key_W))

        # view
        self.viewMenu = self.menuBar().addMenu('&View')
        self.actLoadStandardState = QAction('&Load default view')
        self.viewMenu.addAction(self.actLoadStandardState)
        self.actLoadStandardState.triggered.connect(self.loadStandardDockState)
        self.actShowCoords = QAction("&Show coordinates", self)
        self.actShowCoords.setCheckable(True)
        self.actShowCoords.setChecked(
            self._settings.value("view/show_coordinates") == "True"
        )
        self.viewMenu.addAction(self.actShowCoords)
        self.actShowCoords.changed.connect(self.updateShowCoordsSetting)

        # options
        self.optMenu = self.menuBar().addMenu('&Options')
        self.actIntPoints = QAction("&Interpolation points", self)
        self.actIntPoints.triggered.connect(self.setIntPoints)
        self.optMenu.addAction(self.actIntPoints)
        self.actTimerTime = QAction("&Timer time", self)
        self.optMenu.addAction(self.actTimerTime)
        self.actTimerTime.triggered.connect(self.setTimerTime)

        # experiment
        self.expMenu = self.menuBar().addMenu('&Experiment')
        self.connMenu = self.menuBar().addMenu('&Connections')

        # TODO
        # Verbindungstypen laden
        # Optionen werden  erst beim connect gesetzt
        # Menüeinträge einarbeiten
        # bei 1x serial, default com port laden, wenn möglich
        #
        availableConns = getRegisteredConnections()
        if availableConns:
            for cls, name in availableConns:
                self._logger.info("Found Connection: {}".format(name))
                self.connections[cls] = {}
        else:
            self._logger.error("No Connections found, return!")
            return

        for conn, connInstance in self.connections.items():
            if issubclass(conn, SerialConnection):
                serialMenu = self.connMenu.addMenu(conn.__name__)
                self._getSerialMenu(serialMenu, conn.settings)
            elif issubclass(conn, TcpConnection):
                actConnTcpType = self._getTcpMenu(conn.settings)
                self.connMenu.addMenu(actConnTcpType)
            else:
                self._logger.warning("Cannot handle the connection type!")
            self.connMenu.addSeparator()

        self.expMenu.addSeparator()
        self.actConnect = QAction('&Versuchsaufbau verbinden')
        self.actConnect.setIcon(QIcon(get_resource("connected.png")))
        self.actConnect.setShortcut(QKeySequence("F9"))
        self.expMenu.addAction(self.actConnect)
        self.actConnect.triggered.connect(self.connect)

        self.actDisconnect = QAction('&Versuchsaufbau trennen')
        self.actDisconnect.setEnabled(False)
        self.actDisconnect.setIcon(QIcon(get_resource("disconnected.png")))
        self.actDisconnect.setShortcut(QKeySequence("F10"))
        self.actSendParameter = QAction('&Parameter senden')
        self.actSendParameter.setEnabled(False)
        self.actSendParameter.setShortcut(QKeySequence("F8"))
        self.expMenu.addAction(self.actSendParameter)
        self.actSendParameter.triggered.connect(self.sendParameter)
        self.expMenu.addAction(self.actDisconnect)
        self.actDisconnect.triggered.connect(self.disconnect)
        self.expMenu.addSeparator()
        self.expMenu.addAction(self.actStartExperiment)
        self.expMenu.addAction(self.actStopExperiment)
        self.expMenu.addAction(self.actSendParameter)

        # toolbar
        self.toolbarExp = QToolBar("Experiment")
        self.toolbarExp.setContextMenuPolicy(Qt.PreventContextMenu)
        self.toolbarExp.setMovable(False)
        self.toolbarExp.setIconSize(icon_size)
        self.addToolBar(self.toolbarExp)
        self.toolbarExp.addAction(self.actConnect)
        self.toolbarExp.addAction(self.actDisconnect)
        self.toolbarExp.addSeparator()
        self.toolbarExp.addAction(self.actStartExperiment)
        self.toolbarExp.addAction(self.actStopExperiment)

        self._currentTimerTime = 10
        self._currentInterpolationPoints = 10

        self._currentExpListItem = None
        self._currentLastMeasItem = None
        self._currentDataPointBuffers = None
        self.plotCharts = []

        if not self.loadExpFromFile(fileName):
            return

        self.exp = ExperimentInteractor(self.targetView, self)
        self.exp.sendData.connect(self.writeToConnection)
        self.runExp.connect(self.exp.runExperiment)
        self.stopExp.connect(self.exp.stopExperiment)
        self.exp.expFinished.connect(self.saveLastMeas)

        self._updateExperimentsList()

        self._applyFirstExperiment()

    def _getSerialMenu(self, serialMenu, settings):
        # port
        portMenu = serialMenu.addMenu("Port")
        portMenu.aboutToShow.connect(lambda: self.getComPorts(settings, portMenu))

        # baud
        baudMenu = serialMenu.addMenu("Baud")
        baudMenu.aboutToShow.connect(lambda: self.getBauds(settings, baudMenu))

        return serialMenu

    def getBauds(self, settings, connMenu):
        def setBaud(baud):
            def fn():
                settings['baud'] = baud

            return fn

        baud = settings['baud']
        bauds = ['1200', '2400', '4800', '9600', '14400', '19200', '28800', '38400', '57600', '115200']
        connMenu.clear()
        for _baud in bauds:
            baudAction = QAction(_baud, self)
            baudAction.setCheckable(True)
            baudAction.setChecked(True if _baud in str(baud) else False)
            baudAction.triggered.connect(setBaud(_baud))
            connMenu.addAction(baudAction)

    def getComPorts(self, settings, connMenu):
        def setPort(port):
            def fn():
                settings['port'] = port
            return fn

        port = settings['port']
        connMenu.clear()
        comPorts = serial.tools.list_ports.comports()
        if comPorts:
            if port == '':
                arduinoPorts = [p.device for p in comPorts if 'Arduino' in p.description]
                if len(arduinoPorts) != 0:
                    port = arduinoPorts[0]
            for p in comPorts:
                portAction = QAction(p.device, self)
                portAction.setCheckable(True)
                portAction.setChecked(True if p.device in port else False)
                portAction.triggered.connect(setPort(p.device))
                connMenu.addAction(portAction)
        else:
            noAction = QAction("(None)", self)
            noAction.setEnabled(False)
            connMenu.addAction(noAction)

    def _updateExperimentsList(self):
        self.experimentList.clear()
        for exp in self._experiments:
            self._logger.debug("Add '{}' to experiment list".format(exp["Name"]))
            self.experimentList.addItem(exp["Name"])

    def setIntPoints(self):
        self._settings.beginGroup('plot')
        intPoints, ok = DataIntDialog.getData(min=2, max=500, current=self._settings.value("interpolation_points"))

        if ok:
            self._settings.setValue("interpolation_points", int(intPoints))
            self._logger.info("Set interpolation points to {}".format(intPoints))

        self._settings.endGroup()

    def setTimerTime(self):
        self._settings.beginGroup('plot')
        timerTime, ok = DataIntDialog.getData(min=2, max=10000, current=self._settings.value("timer_time"))

        if ok:
            self._settings.setValue("timer_timer", int(timerTime))
            self._logger.info("Set timer time to {}".format(timerTime))

        self._settings.endGroup()

    def _addSetting(self, group, setting, value):
        """
        Add a setting, if settings is present, no changes are made.

        :param setting (str): Setting to add.
        :param value: Value to be set.
        """
        if not self._settings.contains(group + '/' + setting):
            self._settings.beginGroup(group)
            self._settings.setValue(setting, value)
            self._settings.endGroup()

    def _initSettings(self):
        """
        Provide initial settings for the config file.

        """
        # view management
        self._addSetting("view", "show_coordinates", "True")

        # plot management
        self._addSetting("plot", "interpolation_points", 10000)
        self._addSetting("plot", "timer_time", 10000)

        # log management
        self._addSetting("log_colors", "CRITICAL", "#DC143C")
        self._addSetting("log_colors", "ERROR", "#B22222")
        self._addSetting("log_colors", "WARNING", "#DAA520")
        self._addSetting("log_colors", "INFO", "#101010")
        self._addSetting("log_colors", "DEBUG", "#4682B4")
        self._addSetting("log_colors", "NOTSET", "#000000")

        # plot management
        self._addSetting("plot_colors", "blue", "#1f77b4")
        self._addSetting("plot_colors", "orange", "#ff7f0e")
        self._addSetting("plot_colors", "green", "#2ca02c")
        self._addSetting("plot_colors", "red", "#d62728")
        self._addSetting("plot_colors", "purple", "#9467bd")
        self._addSetting("plot_colors", "brown", "#8c564b")
        self._addSetting("plot_colors", "pink", "#e377c2")
        self._addSetting("plot_colors", "gray", "#7f7f7f")
        self._addSetting("plot_colors", "olive", "#bcbd22")
        self._addSetting("plot_colors", "cyan", "#17becf")

    def updateCoordInfo(self, pos, widget, coordItem):
        mouseCoords = widget.getPlotItem().vb.mapSceneToView(pos)
        coordItem.setPos(mouseCoords.x(), mouseCoords.y())
        coord_text = "x={:.3e} y={:.3e}".format(mouseCoords.x(),
                                                mouseCoords.y())
        self.coordLabel.setText(coord_text)

        show_info = self._settings.value("view/show_coordinates") == "True"
        if widget.sceneBoundingRect().contains(pos) and show_info:
            coordItem.setText(coord_text.replace(" ", "\n"))
            coordItem.show()
        else:
            coordItem.hide()

    # event functions
    def addPlotTreeItem(self, default=False):
        text = "plot_{:03d}".format(self.dataPointTreeWidget.topLevelItemCount())
        if not default:
            name, ok = QInputDialog.getText(self,
                                            "PlotTitle",
                                            "PlotTitle:",
                                            text=text)
            if not (ok and name):
                return
        else:
            name = text

        similarItems = self.dataPointTreeWidget.findItems(name, Qt.MatchExactly)
        if similarItems:
            self._logger.error("Name '{}' existiert bereits".format(name))
            return

        toplevelitem = QTreeWidgetItem()
        toplevelitem.setText(0, name)
        self.dataPointTreeWidget.addTopLevelItem(toplevelitem)
        toplevelitem.setExpanded(1)

    def removeSelectedPlotTreeItems(self):
        items = self.dataPointTreeWidget.selectedItems()
        if not items:
            self._logger.error("Cannot delete plot: No plot selected.")
            return

        for item in items:
            self.removePlotTreeItem(item)

    def removePlotTreeItem(self, item):
        # get the  top item
        while item.parent():
            item = item.parent()

        text = "The labeled plot '" + item.text(0) + "' will be deleted!"
        buttonReply = QMessageBox.warning(self, "Plot delete", text, QMessageBox.Ok | QMessageBox.Cancel)
        if buttonReply == QMessageBox.Ok:
            openDocks = [dock.title() for dock in self.findAllPlotDocks()]
            if item.text(0) in openDocks:
                self.area.docks[item.text(0)].close()

            self.dataPointTreeWidget.takeTopLevelItem(self.dataPointTreeWidget.indexOfTopLevelItem(item))

    def addDatapointToTree(self):
        if not self.dataPointListWidget.selectedIndexes():
            self._logger.error("Cannot add datapoint: No datapoint selected.")
            return

        toplevelItems = self.dataPointTreeWidget.selectedItems()
        if not toplevelItems:
            if self.dataPointTreeWidget.topLevelItemCount() < 2:
                if self.dataPointTreeWidget.topLevelItemCount() < 1:
                    self.addPlotTreeItem(default=True)
                toplevelItem = self.dataPointTreeWidget.topLevelItem(0)
            else:
                self._logger.error("Cannot add dataset: No dataset selected.")
                return
        else:
            toplevelItem = toplevelItems[0]

        while toplevelItem.parent():
            toplevelItem = toplevelItem.parent()

        topLevelItemList = []
        for i in range(toplevelItem.childCount()):
            topLevelItemList.append(toplevelItem.child(i).text(1))

        for idx, dataPoint in enumerate(self.dataPointListWidget.selectedItems()):
            if dataPoint.text() not in topLevelItemList:
                child = QTreeWidgetItem()
                child.setText(1, dataPoint.text())

                toplevelItem.addChild(child)

        for i in range(toplevelItem.childCount()):
            self._settings.beginGroup('plot_colors')
            cKeys = self._settings.childKeys()
            colorIdxItem = i % len(cKeys)
            colorItem = QColor(self._settings.value(cKeys[colorIdxItem]))
            self._settings.endGroup()
            toplevelItem.child(i).setBackground(0, colorItem)

        self.plots(toplevelItem)

    def exportDataPointFromTree(self):
        if not self.dataPointListWidget.selectedIndexes():
            self._logger.error("Can't export data set: no data set selected.")
            return

        if self._currentLastMeasItem is None:
            self._logger.error("Nothing to export!")
            return

        idx = self.lastMeasList.row(self._currentLastMeasItem)
        dataPointBuffers = self.measurements[idx]['dataPointBuffers']

        dataPoints = []
        for item in self.dataPointListWidget.selectedItems():
            for data in dataPointBuffers:
                if data.name == item.text():
                    dataPoints.append(data)
                    continue

        exporter = CSVExporter(dataPoints)
        filename = QFileDialog.getSaveFileName(self, "CSV export", ".csv", "CSV Data (*.csv)")
        if filename[0]:
            exporter.export(filename[0])
            self._logger.info("Export successful.")

    def removeDatapointFromTree(self):
        items = self.dataPointTreeWidget.selectedItems()
        if not items:
            self._logger.error("Cannot delete dataset: No dataset selected.")
            return

        toplevelItem = items[0]
        while toplevelItem.parent():
            toplevelItem = toplevelItem.parent()

        toplevelItem.takeChild(toplevelItem.indexOfChild(self.dataPointTreeWidget.selectedItems()[0]))

        for i in range(toplevelItem.childCount()):
            self._settings.beginGroup('plot_colors')
            cKeys = self._settings.childKeys()
            colorIdxItem = i % len(cKeys)
            colorItem = QColor(self._settings.value(cKeys[colorIdxItem]))
            self._settings.endGroup()
            toplevelItem.child(i).setBackground(0, colorItem)

        self.plots(toplevelItem)

    def plots(self, item):
        title = item.text(0)

        if self._currentLastMeasItem is None:
            self._logger.error("No Measurement to plot!")
            return

        idx = self.lastMeasList.row(self._currentLastMeasItem)
        dataPointBuffers = self.measurements[idx]['dataPointBuffers']

        # check if a top level item has been clicked
        if not item.parent():
            if title in self.nonPlottingDocks:
                self._logger.error("Title '{}' not allowed for a plot window since"
                                   "it would shadow on of the reserved "
                                   "names".format(title))
                return

            # check if plot has already been opened
            openDocks = [dock.title() for dock in self.findAllPlotDocks()]
            if title in openDocks:
                self.updatePlot(item, dataPointBuffers)

    def plotVectorClicked(self, item):

        # check if a top level item has been clicked
        if item.parent():
            return

        title = item.text(0)
        if title in self.nonPlottingDocks:
            self._logger.error("Title '{}' not allowed for a plot window since"
                               "it would shadow on of the reserved "
                               "names".format(title))
            return

        # check if plot has already been opened
        openDocks = [dock.title() for dock in self.findAllPlotDocks()]
        if title in openDocks:
            if self._currentLastMeasItem is None:
                dataPointNames = self.exp.getDataPoints()
                dataPointBuffers = [DataPointBuffer(data) for data in dataPointNames]
            else:
                idx = self.lastMeasList.row(self._currentLastMeasItem)
                dataPointBuffers = self.measurements[idx]['dataPointBuffers']

            self.updatePlot(item, dataPointBuffers)
            try:
                self.area.docks[title].raiseDock()
            except:
                pass
        else:
            self.plotDataVector(item)

    def updatePlot(self, item, dataPointBuffers):
        title = item.text(0)

        # get the new datapoints
        newDataPoints = []
        for indx in range(item.childCount()):
            for dataPoint in dataPointBuffers:
                if dataPoint.name == item.child(indx).text(1):
                    newDataPoints.append(dataPoint)

        # set the new datapoints
        for chart in self.plotCharts:
            if chart.title == title:
                chart.clear()
                for dataPoint in newDataPoints:
                    chart.addPlotCurve(dataPoint)
                chart.updatePlot()
                break

    def plotDataVector(self, item):
        title = str(item.text(0))

        # create plot widget
        widget = PlotWidget()
        chart = PlotChart(title, self._settings)
        chart.plotWidget = widget
        widget.showGrid(True, True)
        widget.getPlotItem().getAxis("bottom").setLabel(text="Time", units="s")

        if self._currentLastMeasItem is None:
            dataPointNames = self.exp.getDataPoints()
            dataPointBuffers = [DataPointBuffer(data) for data in dataPointNames]
        else:
            idx = self.lastMeasList.row(self._currentLastMeasItem)
            dataPointBuffers = self.measurements[idx]['dataPointBuffers']

        for idx in range(item.childCount()):
            for datapoint in dataPointBuffers:
                if datapoint.name == item.child(idx).text(1):
                    chart.addPlotCurve(datapoint)

        # before adding the PlotChart object to the list check if the plot contains any data points
        if chart.dataPoints is not None:
            self.plotCharts.append(chart)
        else:
            return

        chart.updatePlot()

        coordItem = TextItem(text='', anchor=(0, 1))
        widget.getPlotItem().addItem(coordItem, ignoreBounds=True)

        def infoWrapper(pos):
            self.updateCoordInfo(pos, widget, coordItem)

        widget.scene().sigMouseMoved.connect(infoWrapper)

        widget.scene().contextMenu = [QAction("Export png", self),
                                      QAction("Export csv", self)]
        widget.scene().contextMenu[0].triggered.connect(lambda: self.exportPng(widget.getPlotItem(), title, coordItem))
        widget.scene().contextMenu[1].triggered.connect(lambda: self.exportCsv(chart, title))

        # create dock container and add it to dock area
        dock = Dock(title, closable=True)
        dock.addWidget(widget)
        dock.sigClosed.connect(self.closedDock)

        plotWidgets = self.findAllPlotDocks()
        if plotWidgets:
            self.area.addDock(dock, "above", plotWidgets[0])
        else:
            self.area.addDock(dock, "bottom", self.animationDock)

    def closedDock(self):
        """
        Gets called when a dock was closed, if it was a plot dock remove the corresponding PlotChart object
        form the list

        """
        openDocks = [dock.title() for dock in self.findAllPlotDocks()]
        for indx, plot in enumerate(self.plotCharts):
            if not plot.title in openDocks:
                self.plotCharts.pop(indx)

    def exportCsv(self, chart, name):
        exporter = CSVExporter(chart.dataPoints)
        filename = QFileDialog.getSaveFileName(self, "CSV export", name + ".csv", "CSV Data (*.csv)")
        if filename[0]:
            exporter.export(filename[0])

    def exportPng(self, plotItem, name, coordItem):
        # Notwendig da Fehler in PyQtGraph
        exporter = exporters.ImageExporter(plotItem)
        oldGeometry = plotItem.geometry()
        plotItem.setGeometry(QRectF(0, 0, 1920, 1080))

        bgBrush = mkBrush('w')
        exporter.params.param('background').setValue(bgBrush.color())
        exporter.params.param('width').setValue(1920, blockSignal=exporter.widthChanged)
        exporter.params.param('height').setValue(1080, blockSignal=exporter.heightChanged)

        flag = 0
        if coordItem.isVisible():
            coordItem.hide()
            flag = 1

        filename = QFileDialog.getSaveFileName(self, "PNG export", name + ".png", "PNG Image (*.png)")
        if filename[0]:
            exporter.export(filename[0])

        # restore old state
        if flag == 1:
            coordItem.show()
        plotItem.setGeometry(QRectF(oldGeometry))

    @pyqtSlot(QModelIndex)
    def targetViewChanged(self, index):
        self.targetView.resizeColumnToContents(0)

    @pyqtSlot()
    def updateShowCoordsSetting(self):
        self._settings.setValue("view/show_coordinates", str(self.actShowCoords.isChecked()))

    @pyqtSlot()
    def startExperiment(self):
        """
        start the experiment and disable start button
        """
        self._currentExperimentIndex = self.experimentList.row(self._currentExpListItem)
        self._currentExperimentName = self._experiments[self._currentExperimentIndex]["Name"]

        self._settings.beginGroup('plot')
        self._currentInterpolationPoints = self._settings.value("interpolation_points")
        self._currentTimerTime = self._settings.value("timer_time")
        self._settings.endGroup()

        if self._currentExperimentIndex is None:
            expName = ""
        else:
            expName = str(self.experimentList.item(self._currentExperimentIndex).text())

        self._logger.info("Experiment: {}".format(expName))

        self.actStartExperiment.setDisabled(True)
        self.actStopExperiment.setDisabled(False)
        self.actSendParameter.setDisabled(False)
        if self._currentExperimentIndex is not None:
            self.experimentList.item(self._currentExperimentIndex).setBackground(QBrush(Qt.darkGreen))
            self.experimentList.repaint()

        dataPointNames = self.exp.getDataPoints()

        if dataPointNames:
            self._currentDataPointBuffers = [DataPointBuffer(data) for data in dataPointNames]
        else:
            return

        for chart in self.plotCharts:
            chart.setInterpolationPoints(self._currentInterpolationPoints)
            chart.updatePlot()

        data = {}
        data.update({'dataPointBuffers': self._currentDataPointBuffers})
        data.update({'exp': deepcopy(self.exp.getExperiment())})
        self.measurements.append(data)

        item = QListWidgetItem(str(self.lastMeasList.count() + 1) + ": "
                               + self._currentExperimentName + " ~current~")
        self.lastMeasList.addItem(item)
        self.loadLastMeas(item)

        for conn, connInstance in self.connections.items():
            connInstance.doRead = True

        self.timer.start(int(self._currentTimerTime))
        self.exp.runExperiment()

    @pyqtSlot()
    def stopExperiment(self):
        self.actStartExperiment.setDisabled(False)
        self.actStopExperiment.setDisabled(True)
        self.actSendParameter.setDisabled(True)
        for i in range(self.experimentList.count()):
            self.experimentList.item(i).setBackground(QBrush(Qt.white))
        self.experimentList.repaint()

        for conn, connInstance in self.connections.items():
            connInstance.doRead = False

        self.timer.stop()
        self.exp.stopExperiment()

        for conn, connInstance in self.connections.items():
            connInstance.clear()

    def sendParameter(self):
        if self._currentExperimentIndex == self.experimentList.row(
                self._currentExpListItem) and "~current~" in self._currentLastMeasItem.text():
            self.exp.sendParameterExperiment()
        else:
            self._logger.warning("Selected Experiment '{}' doesn't match current running Experiment '{}'!".format(
                self._currentExperimentName,
                self._experiments[self.experimentList.row(self._currentExpListItem)]["Name"]))

    def loadExpFromFile(self, fileName):
        """
        load experiments from file
        :param file_name:
        """
        success = True
        if fileName is None:
            if os.path.isfile('default.sreg'):
                fileName = 'default.sreg'
            else:
                self._logger.error('No default.sreg found!')
                success = False
        else:
            if not os.path.isfile(fileName):
                self._logger.error('Config file {} does not exists!'.format(fileName))
                success = False

        experimentsFileName = os.path.split(fileName)[0]
        self._logger.info("Load config file: {0}".format(experimentsFileName))
        with open(fileName.encode(), "r") as f:
            self._experiments = yaml.load(f)

        self._logger.info("Lade {} Experimente".format(len(self._experiments)))

        return success

    def _applyFirstExperiment(self):
        """
        Apply the first experiment update the experiment index.

        Returns:
            bool: `True` if successful, `False` if errors occurred.
        """
        idx = 0

        # apply
        success = self._applyExperimentByIdx(idx)
        self._currentExpListItem = self.experimentList.item(idx)

        self.setQListItemBold(self.experimentList, self._currentExpListItem, success)
        self.setQListItemBold(self.lastMeasList, self._currentExpListItem, success)

        dataPointNames = self.exp.getDataPoints()

        if dataPointNames:
            self.updateDataPoints(dataPointNames)

        return success

    @pyqtSlot(QListWidgetItem)
    def experimentDclicked(self, item):
        """
        Apply the selected experiment to the current target and set it bold.
        """
        success = self._applyExperimentByIdx(self.experimentList.row(item))
        self._currentExpListItem = item

        self.setQListItemBold(self.experimentList, item, success)
        self.setQListItemBold(self.lastMeasList, item, success)

        dataPointNames = self.exp.getDataPoints()

        if dataPointNames:
            self.updateDataPoints(dataPointNames)

    def _applyExperimentByIdx(self, index=0):
        """
        Apply the given experiment.
        Args:
            index(int): Index of the experiment in the `ExperimentList` .
        Returns:
            bool: `True` if successful, `False` if errors occurred.
        """
        if index >= len(self._experiments):
            self._logger.error("applyExperiment: index error! ({})".format(index))
            return False

        expName = self._experiments[index]["Name"]
        self._logger.info("Experiment '{}' applied".format(expName))

        if self.isConnected:
            # check if experiment runs
            if not self.actStopExperiment.isEnabled():
                self.actStartExperiment.setDisabled(False)

        return self.exp.setExperiment(self._experiments[index])

    def closeEvent(self, QCloseEvent):
        if self.isConnected:
            for conn, connInstance in self.connections.items():
                connInstance.disconnect()
        self._logger.info("Close Event received, shutting down.")
        logging.getLogger().removeHandler(self.textLogger)
        super().closeEvent(QCloseEvent)

    @pyqtSlot()
    def connect(self):
        for conn, _ in self.connections.items():
            connInstance = conn()
            self.connections[conn] = connInstance
            if connInstance.connect():
                self._logger.info("Connection for {} established!".format(conn))
                self.actConnect.setEnabled(False)
                self.actDisconnect.setEnabled(True)
                if self._currentExpListItem is not None:
                    self.actStartExperiment.setEnabled(True)
                self.actStopExperiment.setEnabled(False)
                self.statusbarLabel.setText("Connected!")
                self.connection.received.connect(self.updateData)
                self.connection.start()
                self.isConnected = True
            else:
                self._logger.warning("No connection for {} established! Check your settings!".format(conn))
                self.isConnected = False
                return

    def writeToConnection(self, data):
        for conn, connInstance in self.connections.items():
            if connInstance:
                connInstance.writeData(data)
        else:
            self._logger.error('Keine Verbindung vorhanden!')

    @pyqtSlot()
    def disconnect(self):
        if self.actStopExperiment.isEnabled():
            self.stopExperiment()

        for conn, connInstance in self.connections.items():
            connInstance.disconnect()
            connInstance.received.disconnect()
            self.connections[conn] = None
        self.actConnect.setEnabled(True)
        self.actDisconnect.setEnabled(False)
        self.actStartExperiment.setEnabled(False)
        self.actStopExperiment.setEnabled(False)
        self.statusbarLabel.setText('Not Connected')
        self.isConnected = False

    def findAllPlotDocks(self):
        list = []
        for title, dock in self.area.findAll()[1].items():
            if title in self.nonPlottingDocks:
                continue
            else:
                list.append(dock)

        return list

    def updateData(self, frame):
        data = self.exp.handleFrame(frame)
        if data is None:
            return
        time = data['Zeit'] / 1000.0
        dataPoints = data['Punkte']
        names = data['Punkte'].keys()

        for buffer in self._currentDataPointBuffers:
            if buffer.name in names:
                buffer.addValue(time, dataPoints[buffer.name])

    def updateDataPlots(self):
        if self.visualizer:
            # TODO muss noch geupdated werden
            pass

        for chart in self.plotCharts:
            chart.updatePlot()

    def loadStandardDockState(self):
        self.plotCharts.clear()

        for dock in self.findAllPlotDocks():
            dock.close()
        self.area.restoreState(self.standardDockState)

    def saveLastMeas(self):
        if self._currentExperimentIndex is None:
            return

        items = self.lastMeasList.findItems('~current~', Qt.MatchContains)
        if len(items) != 1:
            self._logger.warning('Error, more than one ~current~ measurement available. Using first one!')

        item = items[0]
        item.setText(item.text().replace(' ~current~', ''))

        idx = self.lastMeasList.row(item)
        self.measurements[idx].update({'dataPointBuffers': deepcopy(self._currentDataPointBuffers)})

    def loadLastMeas(self, item):
        expName = str(item.text())
        self._currentLastMeasItem = item
        try:
            idx = self.lastMeasList.row(item)
        except ValueError:
            self._logger.error("loadLastMeas(): No measurement called '{0}".format(expName))
            return False

        if idx >= len(self.measurements):
            self._logger.error("loadLastMeas(): Invalid index '{}')".format(idx))
            return False

        self._logger.info("Restore of measurement '{}'".format(expName))

        measurement = self.measurements[idx]

        success = self.exp.setExperiment(measurement['exp'])

        self.setQListItemBold(self.lastMeasList, item, success)
        self.setQListItemBold(self.experimentList, item, success)

        dataPointNames = self.exp.getDataPoints()
        dataPointBuffers = measurement['dataPointBuffers']
        if dataPointNames:
            self.updateDataPoints(dataPointNames)

        for i in range(self.dataPointTreeWidget.topLevelItemCount()):
            self.updatePlot(self.dataPointTreeWidget.topLevelItem(i), dataPointBuffers)

        self._logger.info("Apply measurement '{}'".format(measurement['exp']['Name']))

    def updateDataPoints(self, dataPointNames):
        if dataPointNames:
            self.dataPointListWidget.clear()
            self.dataPointListWidget.addItems(dataPointNames)

    def setQListItemBold(self, qList=None, item=None, state=True):
        for i in range(qList.count()):
            newfont = qList.item(i).font()
            if qList.item(i) == item and state:
                newfont.setBold(1)
            else:
                newfont.setBold(0)
            qList.item(i).setFont(newfont)
        qList.repaint()
