# -*- coding: utf-8 -*-
from abc import ABCMeta, abstractmethod

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import *
import time
import os

__all__ = ["VtkVisualizer", "MplVisualizer"]

try:
    import vtk
    from vtk.qt.QVTKRenderWindowInteractor import *

    class QVTKRenderWindowInteractor(QVTKRenderWindowInteractor):
        """
        overload class to patch problem with zooming in vtk window
        the reason is that the QWheelEvent in PyQt5 hasn't the function delta()
        so we have to replace that with angleDelta()
        the error is caused by vtk 7.0.0
        """
        # override function
        def wheelEvent(self, ev):
            if ev.angleDelta().y() >= 0:
                self._Iren.MouseWheelForwardEvent()
            else:
                self._Iren.MouseWheelBackwardEvent()

except ImportError as e:
    QVTKRenderWindowInteractor = None


class Visualizer(metaclass=ABCMeta):
    """
    Base Class for animation
    """

    def __init__(self):
        pass

    @abstractmethod
    def update(self, dataPoints):
        """
        Abstract method to update the canvas with new measurement values.
        :param dataPoints: All the measured data points
        """
        pass


class MplVisualizer(Visualizer):
    """
    Base Class with some function the help visualizing the system using matplotlib
    """

    def __init__(self, qWidget, qLayout):
        Visualizer.__init__(self)
        self.qWidget = qWidget
        self.qLayout = qLayout
        self.dpi = 100
        self.fig = Figure((5.0, 4.0), facecolor='white', dpi=self.dpi)

        self.canvas = FigureCanvas(self.fig)
        self.canvas.setParent(self.qWidget)
        self.canvasMenu = QMenu(self.qWidget)
        self.qActSaveAnimation = QAction('Save Animation', self.qWidget, checkable=True)
        self.qActSaveAnimation.triggered.connect(self.saveAnimation)
        self.canvasMenu.addAction(self.qActSaveAnimation)
        self.canvas.setContextMenuPolicy(Qt.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self.execCanvasMenu)

        self.axes = self.fig.add_subplot(111)
        self.qLayout.addWidget(self.canvas)
        self.qWidget.setLayout(self.qLayout)

        self.frameCounter = 0
        self.fileNameCounter = 0
        self.timeStamp = 0
        self.picturePath = None

        self.timeStamps = list()

    def saveAnimation(self):
        """
        Resets all animation counters and creates the animation directory and sets first time stamp, if animation is
        started.
        """
        self.frameCounter = 0
        self.fileNameCounter = 0
        if self.qActSaveAnimation.isChecked():
            self.timeStamp = time.ctime().replace(' ', '_') + '_'
            self.timeStamps.append(self.timeStamp)
            self.picturePath = self.createDir('animationPictures')

    def execCanvasMenu(self, pos):
        """
        Shows the context menu at position
        :param pos: local position of right click
        """
        self.canvasMenu.exec_(self.canvas.mapToGlobal(pos))

    def saveIfChecked(self):
        """
        Must be called after self.draw_idle() in implementation.
        :return:
        """
        if self.qActSaveAnimation.isChecked():
            fileName = self.picturePath + os.path.sep + self.timeStamp + "%04d" % self.fileNameCounter + '.png'
            self.fig.savefig(fileName, format='png', dpi=self.dpi)
            self.fileNameCounter += 1
            self.frameCounter += 1

    def createDir(self, dirName):
        """
        Checks if directory exists and create the directory if not.
        :param dirName: directory name
        :return: path of directory
        """
        path = os.getcwd() + os.path.sep + dirName
        if not os.path.exists(path) or not os.path.isdir(path):
            os.mkdir(path)
        return path

    def update(self, dataPoints):
        pass


class VtkVisualizer(Visualizer):
    """
    Base Class with some functionality the help visualizing the system using vtk
    """

    def __init__(self, renderer):
        Visualizer.__init__(self)

        assert isinstance(renderer, vtk.vtkRenderer)
        self.ren = renderer

        self.can_reset_view = False
        self.position = None
        self.focal_point = None
        self.view_up = None
        self.view_angle = None
        self.parallel_projection = None
        self.parallel_scale = None
        self.clipping_range = None

    def reset_camera(self):
        """
        Reset camera to original view.

        Will be available if you implement the attributes below and set the
        'can_reset_view' flag.
        """
        if self.can_reset_view:
            camera = self.ren.GetActiveCamera()
            camera.SetPosition(self.position)
            camera.SetFocalPoint(self.focal_point)
            camera.SetViewUp(self.view_up)
            camera.SetViewAngle(self.view_angle)
            camera.SetParallelProjection(self.parallel_projection)
            camera.SetParallelScale(self.parallel_scale)
            camera.SetClippingRange(self.clipping_range)
        else:
            self.ren.ResetCamera()

    def save_camera_pose(self):
        # add camera reset functionality
        camera = self.ren.GetActiveCamera()
        self.position = camera.GetPosition()
        self.focal_point = camera.GetFocalPoint()
        self.view_up = camera.GetViewUp()
        self.view_angle = camera.GetViewAngle()
        self.parallel_projection = camera.GetParallelProjection()
        self.parallel_scale = camera.GetParallelScale()
        self.clipping_range = camera.GetClippingRange()

        self.can_reset_view = True

    def update(self, dataPoints):
        pass
