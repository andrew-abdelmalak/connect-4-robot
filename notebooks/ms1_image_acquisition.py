#!/usr/bin/env python
# coding: utf-8

# In[6]:


import cv2
import matplotlib.pyplot as plt
import time

# 1. Initialize the Webcam
cap = cv2.VideoCapture(0)

if not cap.isOpened():
    print("Error: Could not access the webcam.")
else:
    print("Camera opening... please hold the board in front of the lens.")
    time.sleep(2) # Give the camera a second to adjust light levels
    
    # 2. Capture the frame
    ret, frame = cap.read()
    
    if ret:
        # 3. Save the raw image
        cv2.imwrite('acquired_board_M5.jpg', frame)
        
        # 4. Display in the notebook for your screenshot
        frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        plt.imshow(frame_rgb)
        plt.title("Initial Image Acquisition (Webcam)")
        plt.axis('off')
        plt.show()
        print("Success! 'acquired_board.jpg' has been saved.")
    
    cap.release()


# In[ ]:




