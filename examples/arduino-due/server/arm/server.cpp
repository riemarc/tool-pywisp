/** @file server.ino
 *
 */

// Includes
#include "DueTimer.h"
#include "Transport.h"

//----------------------------------------------------------------------

// System parameter
const unsigned long lDt = 100;              ///< Sampling step [ms]
const unsigned long lKeepalive = 500;       ///< keepalive time [ms]
//----------------------------------------------------------------------

int Ausgang_A = 22;
int Ausgang_B = 23;
int Ausgang_C = 24;
int Ausgang_D = 25;
int Eingang_E = A11;
int sensorValue;
int yGes=186; int xGes=246;
double x = 0;
double y = 0;


float Messung() {
  digitalWrite(Ausgang_B, HIGH);
  digitalWrite(Ausgang_D, LOW);

  /*H1 = S2_AS5145B.encoder_degrees();//-212; // Laufzeit: 125us
  Winkel_1 = H1*3.1416/180;*/

  // Input on analog pin A11:
  sensorValue = analogRead(Eingang_E);
  y = ((float)yGes*((float)sensorValue-278.0)/(691.0-278.0)-yGes/2+2)*0.001;
  /*if (abs(y-y_A) > 0.02) {
      y = y_A;
  }
  y = lowpassFiltery.input(y);*/


  digitalWrite(Ausgang_B, LOW);
  digitalWrite(Ausgang_D, HIGH);

  /*H2 = S1_AS5145B.encoder_degrees();//-168;
  Winkel_2 = H2*3.1416/180;*/


  // Input on analog pin A11:
  sensorValue = analogRead(Eingang_E);
  x = (-(float)xGes*((float)sensorValue-258.0)/(718.0-258.0)+xGes/2+6)*0.001;
  /*if (abs(x-x_A) > 0.02) {
      x = x_A;
  }
  x = lowpassFilterx.input(x);*/


return x;
}


void block(int i){
        pinMode(LED_BUILTIN, OUTPUT);

        for(int k = 0; k < i; k++) {
        digitalWrite(LED_BUILTIN, HIGH);
        delay(1000);
        digitalWrite(LED_BUILTIN, LOW);
        }

        while (1)
        {

        }
    }

bool ledOn = false;
void blink(){
	ledOn = !ledOn;

	digitalWrite(LED_BUILTIN, ledOn); // Led on, off, on, off...
}

// Communication
Transport transport;
//----------------------------------------------------------------------

/**
 * @brief Method that calculates a trajectory value and writes the return value in _trajData->dOutput
 * @param _benchData pointer to test rig data struct
 * @param _trajData pointer to trajectory struct
 */
void fTrajectory(struct Transport::benchData *_benchData, struct Transport::trajData *_trajData) {
    /*if (_benchData->lTime < _trajData->lStartTime) {
        _trajData->dOutput = _trajData->dStartValue;
    } else {
        if (_benchData->lTime < _trajData->lEndTime) {
            double dM = (_trajData->dEndValue - _trajData->dStartValue) / (_trajData->lEndTime - _trajData->lStartTime);
            double dN = _trajData->dEndValue - dM * _trajData->lEndTime;
            _trajData->dOutput = dM * _benchData->lTime + dN;
        } else {
            _trajData->dOutput = _trajData->dEndValue;
        }
    }*/
    x=Messung();
    _trajData->dOutput = x;
}
//----------------------------------------------------------------------

/**
 * @brief continuous loop. Is called by Timer1 every \ref lDt milliseconds.
 */
void fContLoop() {

    interrupts();
    if (transport.runExp()) {
        transport._benchData.lTime += lDt;

        fTrajectory(&transport._benchData, &transport._trajData);

        transport.sendData();

        // handle keepalive signal
        if (lKeepalive != 0 && transport._benchData.lTime > transport.keepaliveTime + lKeepalive) {
            transport.reset();
        }
    }
}
//----------------------------------------------------------------------

/**
 * @brief (arduino function)
 * Initializes Transport protocol, the Timer, and the sensors.
 */
void setup() {
    transport.init();

    // initialize Timer
    Timer3.attachInterrupt(fContLoop);
	Timer3.start(lDt * 1000); // Calls every 50ms
}
//----------------------------------------------------------------------

/**
 * @brief (arduino function)
 * Main Loop
 */
void loop() {
    transport.run();
}
//----------------------------------------------------------------------
