# Arduino Make file. Refer to https://github.com/sudar/Arduino-Makefile

BOARD_TAG = arduino_due_x
ARCHITECTURE = sam

ifeq ($(OS),Windows_NT)
ARDUINO_DIR   	= /c/Arduino/
MONITOR_PORT	= com4

include $(USERPROFILE)/Documents/Arduino-Makefile/Sam.mk

else

include ../../../../../../Arduino-Makefile/Sam.mk

endif
