from datetime import datetime
from logger import logger
from kmlhandler import getkmpoints, kmplush
from gopro7gpshandler import getpoints, gettimediff
from types import SimpleNamespace
import cv2_functions as cf
import multiprocessing as mp
import time
import darknet
import logging
import sys
import cv2
import numpy as np
import os
import signal

# {'latitude': 24.3414826, 'longitude': 120.6253246, 'elevation': 72.024, 'time': datetime.datetime(2020, 3, 19, 17, 7, 27), 'speed': 7.411}

def signal_handler(sig, frame):
    logging.info(str(os.getpid()) + " terminate...")
    sys.exit(1)

def main(file):
    #designer for fps=60 and goPro7 mp4 Video
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logging.info("{0} System Start at {1}".format(str(os.getpid()), datetime.now().strftime('%Y%m%d_%H%M%S')))
    imgs = mp.Manager().Queue()
    imgds = mp.Manager().Queue()
    detector_ready = mp.Manager().Value('i', False)
    dn_width = mp.Manager().Value('i', 416)
    dn_height = mp.Manager().Value('i', 416)
    mpdarknet = mp.Process(target=detector, args=(imgs, imgds, detector_ready, dn_width, dn_height,))
    mpdarknet.start()
    
    while detector_ready.value == False:
        logging.info("Waiting for detector ready...re-check in 10s")
        time.sleep(10)
    logging.debug("now checking the module")
    kmpoints = getkmpoints()
    logging.info("loaded " + str(len(kmpoints)) + " of HMP(百公尺樁)")
    if(len(kmpoints) < 2):
        logging.warning("not enough HMP for count\r\nExit.....")
        sys.exit(1)
    points = getpoints(file, skip=False)
    logging.info("find " + str(len(points)) + " GPS points in file")
    logging.info("Record time taken " + str(gettimediff(points)))
    if(len(points) < 2):
        logging.warning("not enout GPS point\r\nExit.....")
        sys.exit(1)

    mpsavedat = mp.Process(target=savedata, args=(imgds,kmpoints,))
    mpsavedat.start()

    cap = cv2.VideoCapture(file)
    if(not cap.isOpened()):
        logging.warning("could not open :", file)
        sys.exit(1)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    logging.info(str(total_frames) + " frames in file")

    count = 0
    cur_frame = 0

    for p in points :
        while(imgs.qsize() > 700):
            logging.warning("Pause for waiting detector processing 60s")
            time.sleep(60)
        if(count % 10 == 0):logging.info("{0} imgs in the Queue".format(str(imgs.qsize())))
        count = count + 1
        logging.info("Start to processing point " + str(count))
        cur_point = SimpleNamespace(lat= p.latitude, lon=p.longitude, time=p.time)
        logging.debug(cur_point.__dict__)

        for frame_in_second in range(15):
            if(count == 1 and frame_in_second == 8):
                break
            while(cur_frame % 4 != 0 and cur_frame > 0):
                cap.grab()
                cur_frame = cur_frame + 1
            logging.debug("processing frame " + str(cur_frame))
            success, frame = cap.read()
            if(not success):
                logging.warning("frame {0} read fail skip.....".format(str(cur_frame)))
            else:
                job = cur_point
                job.frame = cv2.resize(frame, (dn_width.value,dn_height.value), interpolation=cv2.INTER_AREA)[...,::-1]
                job.frame_count = cur_frame
                imgs.put(job)
            cur_frame = cur_frame + 1
        if(cur_frame >= total_frames or count == len(points)):
            break
    while(not(imgs.empty() and imgds.empty())):
        logging.debug("Waiting for all jobs done.....")
        time.sleep(10)
    logging.info("Process done")
    
def detector(jobs, imgds, flag, dn_width, dn_height):
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger(nameprefix="darknet")
    logging.info(str(os.getpid()) + " detector start")
    thresh = 0.5
    cfgpath = ".\\darknet_data\\yolov4-tra_416.cfg"
    weipath = ".\\darknet_data\\yolov4-tra_320_best.weights"
    metpath = ".\\darknet_data\\obj.data"
    darknet.performDetect(thresh=thresh, configPath=cfgpath, 
                        weightPath=weipath, metaPath=metpath, initOnly=True)
    darknet_image = darknet.make_image(darknet.network_width(darknet.netMain),
                                    darknet.network_height(darknet.netMain),3)
    dn_width.value = darknet.network_width(darknet.netMain)
    dn_height.value = darknet.network_height(darknet.netMain)
    flag.value = True
    while True:
        if(jobs.empty()):
            time.sleep(1)
            logging.debug("jobs.qisze() = {0} sleep(1)".format(str(jobs.qsize())))
        else:
            #job.lat, job.lon, job.time, job.frame, job.frame_count
            # fps_count_start_time = time.time()
            job = jobs.get()
            logging.debug("job.lat = {0} job.lon = {1} job.time = {2} job.frame.type = {3} job.frame_count = {4}"
                        .format(job.lat, job.lon, job.time, type(job.frame), job.frame_count))
            darknet.copy_image_from_bytes(darknet_image, job.frame.tobytes())
            detections = darknet.detect_image(darknet.netMain, darknet.metaMain, darknet_image, thresh=thresh)
            job.detections = detections
            imgds.put(job)
            # print("detector FPS:" + str(round(1 / (time.time() - fps_count_start_time), 1)))

def savedata(imgds, kmlpoints, debug = False):
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    logger(nameprefix="savedata")
    logging.info(str(os.getpid()) + " savedata process start")
    savesdir = os.path.join(os.path.dirname(__file__), "saves")
    while True:
        if(imgds.empty()):
            time.sleep(1)
            logging.debug("imgds.qisze() = {0} sleep(1)".format(str(imgds.qsize())))
        else:
            job = imgds.get()
            if(job.detections != []):
                logging.info("on frame {0} detected object".format(str(job.frame_count)))
                filename = os.path.join(os.path.dirname(__file__), "saves", str(job.frame_count) + ".jpg")
                logging.debug("frame_count {0}.detections = {1}".format(str(job.frame_count), str(job.detections)))
                msg, job.frame = cf.roiDrawBoxes(job.detections, job.frame)
                if(msg != ""):
                    cv2.imwrite(filename, job.frame[...,::-1])
            elif(job.detections == []):
                filename = os.path.join(os.path.dirname(__file__), "saves", "debug", str(job.frame_count) + ".jpg")
                logging.debug("skip frame_count {0} for detections == {1}".format(str(job.frame_count), str(job.detections)))
                if(debug):cv2.imwrite(filename, job.frame[...,::-1])

if __name__ == "__main__":
    logger(nameprefix="Main")
    file = '.\\gopro2gpx\\gopro7.MP4'
    main(file=file)