#!/bin/bash
# Makes use of the SU2 tutorial: https://su2code.github.io/tutorials/Inviscid_ONERAM6/

# Execute SU2 & write PID to file
SU2_CFD inv_ONERAM6.cfg &
echo $! >/tmp/pid.file

# Excute Simvue monitor
python3 SU.py /tmp/pid.file
