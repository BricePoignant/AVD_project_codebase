#!/usr/bin/env python3
from __future__ import print_function
from __future__ import division

# System level imports
import copy
import sys
import os
import argparse
import logging
import time
import math
import numpy as np
import csv
import matplotlib.pyplot as plt
from numpy.core.defchararray import index
import controller2d
import configparser 
import local_planner
import behavioural_planner
import cv2
import json 
from math import sin, cos, pi, tan, sqrt, atan2

from carla_detector_model_traffic_light import detect_image, get_model_from_file
from postprocessing import draw_boxes
# Script level imports
sys.path.append(os.path.abspath(sys.path[0] + '/..'))
import live_plotter as lv   # Custom live plotting library
from carla            import sensor
from carla.client     import make_carla_client, VehicleControl
from carla.settings   import CarlaSettings
from carla.tcp        import TCPConnectionError
from carla.controller import utils
from carla.sensor import Camera
from carla.image_converter import labels_to_array, depth_to_array, to_bgra_array
from carla.planner.city_track import CityTrack


###############################################################################
# CONFIGURABLE PARAMETERS DURING EXAM
###############################################################################
PLAYER_START_INDEX = 27       #  spawn index for player
DESTINATION_INDEX =  124     # Setting a Destination
NUM_PEDESTRIANS        = 200   # total number of pedestrians to spawn
NUM_VEHICLES           = 50  # total number of vehicles to spawn

SEED_PEDESTRIANS       = 0      # seed for pedestrian spawn randomizer
SEED_VEHICLES          = 0     # seed for vehicle spawn randomizer
###############################################################################

ITER_FOR_SIM_TIMESTEP  = 10     # no. iterations to compute approx sim timestep
WAIT_TIME_BEFORE_START = 1.00   # game seconds (time before controller start)
TOTAL_RUN_TIME         = 5000.00 # game seconds (total runtime before sim end)
TOTAL_FRAME_BUFFER     = 300    # number of frames to buffer after total runtime
CLIENT_WAIT_TIME       = 3      # wait time for client before starting episode
                                # used to make sure the server loads
                                # consistently

WEATHERID = {
    "DEFAULT": 0,
    "CLEARNOON": 1,
    "CLOUDYNOON": 2,
    "WETNOON": 3,
    "WETCLOUDYNOON": 4,
    "MIDRAINYNOON": 5,
    "HARDRAINNOON": 6,
    "SOFTRAINNOON": 7,
    "CLEARSUNSET": 8,
    "CLOUDYSUNSET": 9,
    "WETSUNSET": 10,
    "WETCLOUDYSUNSET": 11,
    "MIDRAINSUNSET": 12,
    "HARDRAINSUNSET": 13,
    "SOFTRAINSUNSET": 14,
}
SIMWEATHER = WEATHERID["CLEARNOON"]     # set simulation weather

FIGSIZE_X_INCHES   = 8      # x figure size of feedback in inches
FIGSIZE_Y_INCHES   = 8      # y figure size of feedback in inches
PLOT_LEFT          = 0.1    # in fractions of figure width and height
PLOT_BOT           = 0.1    
PLOT_WIDTH         = 0.8
PLOT_HEIGHT        = 0.8

DIST_THRESHOLD_TO_LAST_WAYPOINT = 2.0  # some distance from last position before
                                       # simulation ends
DELTA_ORIENTATION=45
# Planning Constants
NUM_PATHS = 7
BP_LOOKAHEAD_BASE      = 16.0            # m
BP_LOOKAHEAD_TIME      = 1.0              # s
PATH_OFFSET            = 1.5        # m
CIRCLE_OFFSETS         = [-1.0, 1.0, 3.0] # m
CIRCLE_RADII           = [1.5, 1.5, 1.5]  # m
TIME_GAP               = 1.0              # s
PATH_SELECT_WEIGHT     = 10
A_MAX                  = 2.5              # m/s^2
SLOW_SPEED             = 2.0              # m/s
STOP_LINE_BUFFER       = 3.5              # m
LEAD_VEHICLE_LOOKAHEAD = 25               # m Treshold at which we stop considering the lead vehicle as an obstacle
LEAD_VEHICLE_ACTIVATION = 13              # m Treshold at which the velocity planner accept the lead vehicle
LP_FREQUENCY_DIVISOR   = 2                # Frequency divisor to make the 
                                          # local planner operate at a lower
                                          # frequency than the controller
                                          # (which operates at the simulation
                                          # frequency). Must be a natural
                                          # number.

# Path interpolation parameters
INTERP_MAX_POINTS_PLOT    = 10   # number of points used for displaying
                                 # selected path
INTERP_DISTANCE_RES       = 0.01 # distance between interpolated points

MAP_OBSTACLE_THRESHOLD =30 # viewing distance of obstacles

MAP_ANGLE_THRESHOLD = 25 # angle treshold in which we accept potential lead vehicles or not

# controller output directory
CONTROLLER_OUTPUT_FOLDER = os.path.dirname(os.path.realpath(__file__)) +\
                           '/controller_output/'

# Camera parameters
camera_parameters = {}
camera_parameters['x'] = 2.3
camera_parameters['y'] = 1.3
camera_parameters['z'] = 1.3
camera_parameters['width'] = 416
camera_parameters['height'] = 416
camera_parameters['fov'] = 60


camera_parameters['yaw'] = 0
camera_parameters['pitch'] = 10
camera_parameters['roll'] = 0

MAX_DEPTH=1000 #default value of traffic light depth

# Model initialization for detector
model = get_model_from_file()

def rotate_x(angle):
    R = np.mat([[ 1,         0,           0],
                 [ 0, cos(angle), -sin(angle) ],
                 [ 0, sin(angle),  cos(angle) ]])
    return R

def rotate_y(angle):
    R = np.mat([[ cos(angle), 0,  sin(angle) ],
                 [ 0,         1,          0 ],
                 [-sin(angle), 0,  cos(angle) ]])
    return R

def rotate_z(angle):
    R = np.mat([[ cos(angle), -sin(angle), 0 ],
                 [ sin(angle),  cos(angle), 0 ],
                 [         0,          0, 1 ]])
    return R
# Utils : Rotation - XYZ
def to_rot(r):
    Rx = np.mat([[ 1,         0,           0],
                 [ 0, cos(r[0]), -sin(r[0]) ],
                 [ 0, sin(r[0]),  cos(r[0]) ]])

    Ry = np.mat([[ cos(r[1]), 0,  sin(r[1]) ],
                 [ 0,         1,          0 ],
                 [-sin(r[1]), 0,  cos(r[1]) ]])

    Rz = np.mat([[ cos(r[2]), -sin(r[2]), 0 ],
                 [ sin(r[2]),  cos(r[2]), 0 ],
                 [         0,          0, 1 ]])

    return Rz*Ry*Rx


# Transform the obstacle with its boundary point in the global frame
def obstacle_to_world(location, dimensions, orientation):
    box_pts = []

    x = location.x
    y = location.y
    z = location.z

    yaw = orientation.yaw * pi / 180

    xrad = dimensions.x
    yrad = dimensions.y
    zrad = dimensions.z

    # Border points in the obstacle frame
    cpos = np.array([
            [-xrad, -xrad, -xrad, 0,    xrad, xrad, xrad,  0    ],
            [-yrad, 0,     yrad,  yrad, yrad, 0,    -yrad, -yrad]])
    
    # Rotation of the obstacle
    rotyaw = np.array([
            [np.cos(yaw), np.sin(yaw)],
            [-np.sin(yaw), np.cos(yaw)]])
    
    # Location of the obstacle in the world frame
    cpos_shift = np.array([
            [x, x, x, x, x, x, x, x],
            [y, y, y, y, y, y, y, y]])
    
    cpos = np.add(np.matmul(rotyaw, cpos), cpos_shift)

    for j in range(cpos.shape[1]):
        box_pts.append([cpos[0,j], cpos[1,j]])
    
    return box_pts

def check_for_traffic_light(sensor_data):
    '''
    Check if there is a traffic light and return its label and the associated bounding box.
    label ---->  [0,1,2] = [GO,STOP,NO_TRAFFIC_LIGHT]
    '''
    showing_dims=(416,416)
    if sensor_data.get("CameraRGB", None) is not None:
        # Camera BGR data
        image_BGR = to_bgra_array(sensor_data["CameraRGB"])
        image_BGR2=image_BGR.copy()
        image_RGB = cv2.cvtColor(image_BGR, cv2.COLOR_BGR2RGB)
        image_RGB = cv2.resize(image_RGB, showing_dims)
        image_RGB = image_RGB / 255
        image_RGB = np.expand_dims(image_RGB, 0)
        _, netout = detect_image(image_RGB, image_BGR2, model) #perform object detection
        plt_image=image_BGR
        percentage=0.03 #percentage used to increase the bounding box
        for box in netout:
            label=box.get_label()
            #enlarge the bounding box of the fixed percentage
            box.xmin-=box.xmin*percentage
            box.ymin-=box.ymin*percentage
            box.xmax+=box.xmax*percentage
            box.ymax+=box.ymax*percentage

            plt_image=draw_boxes(image_BGR,[box],["go", "stop"])  #draw enlarged bounding box in image and show it
            cv2.imshow("BGRA_IMAGE", plt_image)
            cv2.waitKey(1)

            return label,box

        cv2.imshow("BGRA_IMAGE", plt_image)
        cv2.waitKey(1)
        return 2,None


def compute_depth_tl(segmentation_data, depth_data, tl_box):
    '''
    Take the bounding box to check if there is a traffic light within, using a segmentation camera. If it is inside, compute
    the depth between the camera and  the traffic light.
    '''
    segmentation_data = labels_to_array(segmentation_data)
    #resize the bounding box according to the camera parameters and perform correction if the bounding box is outside of the image.
    top_left_point, bottom_right_point = (tl_box.xmin, tl_box.ymin), (tl_box.xmax, tl_box.ymax)
    top_left_x = int(top_left_point[0] * 416)
    bottom_right_x = int(bottom_right_point[0] * 416)
    top_left_y = int(top_left_point[1] * 416)
    bottom_right_y = int(bottom_right_point[1] * 416)
    image_w,image_h=camera_parameters['width'],camera_parameters['height']
    if (top_left_x > image_w ): top_left_x = image_w
    if (bottom_right_x > image_w): bottom_right_x = image_w
    if (top_left_y > image_h): top_left_y = image_h
    if ( bottom_right_y > image_h):  bottom_right_y = image_h
    if (top_left_x < 0 ): top_left_x = 0
    if (bottom_right_x < 0): bottom_right_x = 0
    if (top_left_y < 0): top_left_y = 0
    if ( bottom_right_y < 0):  bottom_right_y = 0

    #check if the bounding box contains the traffic light
    found_tl=False
    for i in range(top_left_x, bottom_right_x):
        for j in range(top_left_y, bottom_right_y):
            if segmentation_data[j][i] == 12:
                found_tl=True
                break
        if segmentation_data[j][i] == 12:
            break

    #compute the depth on the [j,1] pixel
    depth_data = depth_to_array(depth_data)
    if found_tl:
        tl_depth = depth_data[j][i] * 1000  # Consider depth in meters
    else:
        tl_depth=1000
    return tl_depth

def emergency_break_pedestrian(ego_state, x_history, y_history, measurement_data, pedestrians_info, bp):
    '''
    compute the bounding box on the ego-vehicle's front bumper in order to check if there are collisions with pedestrian and perform emergency
    brake.
    '''
    ego_ori = ego_state[2]
    dx = x_history[-1] - x_history[-2]
    dy = y_history[-1] - y_history[-2]

    #compute the offset increment to shift the center of bounding box of one meter according to the ego-vehicle orientation
    if dx < -0.1:
        offset_increment_x = -1
    elif dx > 0.1:
        offset_increment_x = 1
    else:
        offset_increment_x = 0
    if dy < -0.1:
        offset_increment_y = -1
    elif dy > 0.1:
        offset_increment_y = 1
    else:
        offset_increment_y = 0

    BB_RADIUS_X,BB_RADIUS_Y=3,3
    #bounding box parameters if the ego-vehicle is turning
    if abs(dy) > 0.1 and abs(dx) > 0.1:
        BB_RADIUS_X = 3
        BB_RADIUS_Y = 3
    #bounding box parameters if the ego-vehicle is not turning
    elif abs(dx) > 0.1:
        if bp._obstacle:
            #bounding box parameters if the ego-vehicle is in DANGEROUS state
            BB_RADIUS_X = 3
            BB_RADIUS_Y = 3
        else:
            #bounding box parameters if the ego-vehicle is not in DANGEROUS state
            BB_RADIUS_X = 7
            BB_RADIUS_Y = 3
    elif abs(dy) > 0.1:
        if bp._obstacle:
            #bounding box parameters if the ego-vehicle is in DANGEROUS state
            BB_RADIUS_Y = 3
            BB_RADIUS_X = 3
        else:
            #bounding box parameters if the ego-vehicle is not in DANGEROUS state
            BB_RADIUS_Y = 7
            BB_RADIUS_X = 3

    x_center_bb = ego_state[0] \
                  + measurement_data.player_measurements.bounding_box.extent.x * cos(ego_ori)+\
                  offset_increment_x

    y_center_bb = ego_state[1] \
                  + measurement_data.player_measurements.bounding_box.extent.y * sin(ego_ori)+ \
                  offset_increment_y

    xmin, xmax, ymin, ymax = x_center_bb - BB_RADIUS_X, x_center_bb + BB_RADIUS_X, y_center_bb - BB_RADIUS_Y, y_center_bb + BB_RADIUS_Y

    #check if a pedestrian with no parallel orientation is within the bounding box
    for p in pedestrians_info:
        p_ori = p[1]
        ped_angle = abs(atan2(sin(ego_ori - p_ori), cos(ego_ori - p_ori))) * 180 / pi
        p_x, p_y = p[0].x, p[0].y
        if 10 <= ped_angle <= 170:

            if xmin < p_x < xmax and ymin < p_y < ymax:
                bp._handbrake = True
                bp._obstacle = True
        else:
             dist=np.sqrt((p_x-x_center_bb-offset_increment_x)**2+(p_y-y_center_bb-offset_increment_y)**2)
             if dist<=1.5:
                 bp._handbrake = True
                 bp._obstacle = True



def check_collision_intersections(bp, cars_collision, in_intersection, percentage=0.7):
    '''
    Predict if there could be collisions in an intersection and perform emergency brake.
    '''
    if len(cars_collision) > 0 and in_intersection:
        cnt_collided_path = 0
        for i in range(len(cars_collision)):
            if not cars_collision[i]:
                cnt_collided_path += 1
        if cnt_collided_path >= len(cars_collision) * percentage:
            bp._handbrake = True

def update_obstacles(bp, measurement_data,current_x,current_y,ego_state):
    '''
    Update all the data of the dynamic obstacles around the ego-vehicle, i.e. orientation and position.
    If a vehicle has its orientation in parallel to that of the ego-vehicle it is considered as a potential lead_vehicle and
    in particular if its distance is less at 10 meters, then the emergency brake  is activated.
    '''

    lead_car_state = []
    bp._follow_lead_vehicle = False
    obstacles = np.empty((0, 2), dtype=float)
    pedestrians = np.empty((0, 2), dtype=float)
    pedestrians_info = []
    cars = np.empty((0, 2), dtype=float)
    for agent in measurement_data.non_player_agents:
        loc = agent.vehicle.transform.location
        #consider only the agent which are cars and within the square of dimension MAP_OBSTACLE_THRESHOLD around ego_vehicle position
        if agent.HasField('vehicle') and (
                current_x - MAP_OBSTACLE_THRESHOLD < loc.x < current_x + MAP_OBSTACLE_THRESHOLD) and (
                current_y - MAP_OBSTACLE_THRESHOLD < loc.y < current_y + MAP_OBSTACLE_THRESHOLD):
            dim = agent.vehicle.bounding_box.extent
            ori = agent.vehicle.transform.rotation
            vehicle_angle = abs(atan2(sin(ego_state[2] - ori.yaw * pi / 180), cos(ego_state[2] - ori.yaw * pi / 180)))
            if (bp._follow_lead_vehicle == False and vehicle_angle * 180 / pi < MAP_ANGLE_THRESHOLD):
                bp._follow_lead_vehicle_lookahead = LEAD_VEHICLE_LOOKAHEAD
                bp.check_for_lead_vehicle(ego_state, [loc.x, loc.y])
            if (bp._follow_lead_vehicle == True and len(lead_car_state) == 0):
                bp._follow_lead_vehicle = False
                bp._follow_lead_vehicle_lookahead = LEAD_VEHICLE_ACTIVATION
                bp.check_for_lead_vehicle(ego_state, [loc.x, loc.y])
                if (bp._follow_lead_vehicle == True):
                    lead_car_state = [loc.x, loc.y, agent.vehicle.forward_speed]
                    dist_lead_car = np.sqrt((loc.x - ego_state[0]) ** 2 + (loc.y - ego_state[1]) ** 2)
                    if dist_lead_car < 10:
                        bp._handbrake = True
            else:
                obstacles = np.vstack((obstacles, np.array(obstacle_to_world(loc, dim, ori))))
                cars = np.vstack((cars, np.array(obstacle_to_world(loc, dim, ori))))

        loc = agent.pedestrian.transform.location
        #consider only the agent which are pedestrians and within the square of dimension MAP_OBSTACLE_THRESHOLD around ego_vehicle position
        if agent.HasField('pedestrian') and (
                current_x - MAP_OBSTACLE_THRESHOLD < loc.x < current_x + MAP_OBSTACLE_THRESHOLD) and (
                current_y - MAP_OBSTACLE_THRESHOLD < loc.y < current_y + MAP_OBSTACLE_THRESHOLD):
            dim = agent.pedestrian.bounding_box.extent
            ori = agent.pedestrian.transform.rotation
            pedestrians_info.append([loc, ori.yaw * pi / 180])
            pedestrians = np.vstack((pedestrians, np.array(obstacle_to_world(loc, dim, ori))))
            obstacles = np.vstack((obstacles, np.array(obstacle_to_world(loc, dim, ori))))
    return obstacles, pedestrians_info, pedestrians, cars, lead_car_state



def manage_intersection(intersection_rectangles, ego_state,measurement_data):
    '''
    verify if the ego-vehicle's front bumper is inside the bounding box built for the intersections.
    '''
    ego_ori=ego_state[2]
    for rectangle in intersection_rectangles:
        ego_head_x = ego_state[0] + measurement_data.player_measurements.bounding_box.extent.x * cos(ego_ori)
        ego_head_y = ego_state[1] + measurement_data.player_measurements.bounding_box.extent.x * sin(ego_ori)

        xmin, xmax, ymin, ymax = rectangle
        if xmin < ego_head_x < xmax and ymin < ego_head_y < ymax:
            return True
    return False


def predict_pedestrian_collisions(pedestrian_collision_check_array, pedestrians_info, ego_state,DELTA_ORIENTATION):
    '''
    Considering all the paths from the Local Planner, if the pedestrians are noticed on the outermost paths and
    have an orientation with values contained in a certain range of possible values, then the collision is predicted;
    otherwise, if the pedestrians are noticed by the central paths regardless of their orientation,then the collision is predicted.
    '''

    #we use a parallel collision array named is_active_collision to predict the collisions.
    if len(pedestrian_collision_check_array) >= 3:
        is_active_collision = [False] * len(pedestrian_collision_check_array)
        mid = int(len(is_active_collision) / 2)
        is_active_collision[mid], is_active_collision[mid - 1], is_active_collision[mid + 1] = True, True, True
    else:
        is_active_collision = [True] * len(pedestrian_collision_check_array)

    if False in pedestrian_collision_check_array:
        if len(pedestrian_collision_check_array) >= 3:
            for p in pedestrians_info:
                p_x, p_y, p_ori = p[0].x, p[0].y, p[1]
                ego_ori = ego_state[2]
                #compute the angle between ego_vehicle and pedestrian p
                ped_angle = abs(atan2(sin(ego_ori - p_ori), cos(ego_ori - p_ori))) * 180 / pi
                #if ped_angle is in [45°,135°] then is going to cross the road
                if 90 - DELTA_ORIENTATION <= ped_angle <= 90 + DELTA_ORIENTATION:
                    for i in range(1, mid):
                        if not is_active_collision[i]:
                            is_active_collision[i] = True
                    for i in range(len(is_active_collision) - 2, mid, -1):
                        if not is_active_collision[i]:
                            is_active_collision[i] = True

        for i in range(len(pedestrian_collision_check_array)):
        #there is a predicted collision if in pedestrian_collision_chek_array[i] is False and is_active_collision[i] is True
            if not pedestrian_collision_check_array[i] and is_active_collision[i]:
                return True,is_active_collision

    return False,is_active_collision


def make_carla_settings(args):
    """Make a CarlaSettings object with the settings we need.
    """
    settings = CarlaSettings()
    
    # There is no need for non-agent info requests if there are no pedestrians
    # or vehicles.
    get_non_player_agents_info = False
    if (NUM_PEDESTRIANS > 0 or NUM_VEHICLES > 0):
        get_non_player_agents_info = True

    # Base level settings
    settings.set(
        SynchronousMode=True,
        SendNonPlayerAgentsInfo=get_non_player_agents_info, 
        NumberOfVehicles=NUM_VEHICLES,
        NumberOfPedestrians=NUM_PEDESTRIANS,
        SeedVehicles=SEED_VEHICLES,
        SeedPedestrians=SEED_PEDESTRIANS,
        WeatherId=SIMWEATHER,
        QualityLevel=args.quality_level)

    # Common cameras settings
    cam_height = camera_parameters['z'] 
    cam_x_pos = camera_parameters['x']
    cam_y_pos = camera_parameters['y']

    cam_yaw = camera_parameters['yaw']
    cam_pitch = camera_parameters['pitch']
    cam_roll = camera_parameters['roll']

    camera_width = camera_parameters['width']
    camera_height = camera_parameters['height']
    camera_fov = camera_parameters['fov']

    # Declare here your sensors
    # RGB Camera
    camera0 = Camera("CameraRGB")
    camera0.set_image_size(camera_width, camera_height)
    camera0.set(FOV=camera_fov)
    camera0.set_position(cam_x_pos, cam_y_pos, cam_height)
    camera0.set_rotation(cam_yaw, cam_pitch, cam_roll)

    settings.add_sensor(camera0)

    # DEPTH Camera
    camera1 = Camera("DepthCamera", PostProcessing="Depth")

    camera1.set_image_size(camera_width, camera_height)
    camera1.set(FOV=camera_fov)
    camera1.set_position(cam_x_pos, cam_y_pos, cam_height)
    camera1.set_rotation(cam_yaw, cam_pitch, cam_roll)

    settings.add_sensor(camera1)
    # SEMANTIC SEG CAMERA
    camera2 = Camera("SegmentationCamera", PostProcessing="SemanticSegmentation")

    camera2.set_image_size(camera_width, camera_height)
    camera2.set(FOV=camera_fov)
    camera2.set_position(cam_x_pos, cam_y_pos, cam_height)
    camera2.set_rotation(cam_yaw, cam_pitch, cam_roll)

    settings.add_sensor(camera2)
    return settings

class Timer(object):
    """ Timer Class
    
    The steps are used to calculate FPS, while the lap or seconds since lap is
    used to compute elapsed time.
    """
    def __init__(self, period):
        self.step = 0
        self._lap_step = 0
        self._lap_time = time.time()
        self._period_for_lap = period

    def tick(self):
        self.step += 1

    def has_exceeded_lap_period(self):
        if self.elapsed_seconds_since_lap() >= self._period_for_lap:
            return True
        else:
            return False

    def lap(self):
        self._lap_step = self.step
        self._lap_time = time.time()

    def ticks_per_second(self):
        return float(self.step - self._lap_step) /\
                     self.elapsed_seconds_since_lap()

    def elapsed_seconds_since_lap(self):
        return time.time() - self._lap_time

def get_current_pose(measurement):
    """Obtains current x,y,yaw pose from the client measurements
    
    Obtains the current x,y, and yaw pose from the client measurements.

    Args:
        measurement: The CARLA client measurements (from read_data())

    Returns: (x, y, yaw)
        x: X position in meters
        y: Y position in meters
        yaw: Yaw position in radians
    """
    x   = measurement.player_measurements.transform.location.x
    y   = measurement.player_measurements.transform.location.y
    z   =  measurement.player_measurements.transform.location.z

    pitch = math.radians(measurement.player_measurements.transform.rotation.pitch)
    roll = math.radians(measurement.player_measurements.transform.rotation.roll)
    yaw = math.radians(measurement.player_measurements.transform.rotation.yaw)

    return (x, y, z, pitch, roll, yaw)

def get_start_pos(scene):
    """Obtains player start x,y, yaw pose from the scene
    
    Obtains the player x,y, and yaw pose from the scene.

    Args:
        scene: The CARLA scene object

    Returns: (x, y, yaw)
        x: X position in meters
        y: Y position in meters
        yaw: Yaw position in radians
    """
    x = scene.player_start_spots[0].location.x
    y = scene.player_start_spots[0].location.y
    yaw = math.radians(scene.player_start_spots[0].rotation.yaw)

    return (x, y, yaw)

def get_player_collided_flag(measurement, 
                             prev_collision_vehicles, 
                             prev_collision_pedestrians,
                             prev_collision_other):
    """Obtains collision flag from player. Check if any of the three collision
    metrics (vehicles, pedestrians, others) from the player are true, if so the
    player has collided to something.

    Note: From the CARLA documentation:

    "Collisions are not annotated if the vehicle is not moving (<1km/h) to avoid
    annotating undesired collision due to mistakes in the AI of non-player
    agents."
    """
    player_meas = measurement.player_measurements
    current_collision_vehicles = player_meas.collision_vehicles
    current_collision_pedestrians = player_meas.collision_pedestrians
    current_collision_other = player_meas.collision_other

    collided_vehicles = current_collision_vehicles > prev_collision_vehicles
    collided_pedestrians = current_collision_pedestrians > \
                           prev_collision_pedestrians
    collided_other = current_collision_other > prev_collision_other

    return (collided_vehicles or collided_pedestrians or collided_other,
            current_collision_vehicles,
            current_collision_pedestrians,
            current_collision_other)

def send_control_command(client, throttle, steer, brake, 
                         hand_brake=False, reverse=False):
    """Send control command to CARLA client.
    
    Send control command to CARLA client.

    Args:
        client: The CARLA client object
        throttle: Throttle command for the sim car [0, 1]
        steer: Steer command for the sim car [-1, 1]
        brake: Brake command for the sim car [0, 1]
        hand_brake: Whether the hand brake is engaged
        reverse: Whether the sim car is in the reverse gear
    """
    control = VehicleControl()
    # Clamp all values within their limits
    steer = np.fmax(np.fmin(steer, 1.0), -1.0)
    throttle = np.fmax(np.fmin(throttle, 1.0), 0)
    brake = np.fmax(np.fmin(brake, 1.0), 0)

    control.steer = steer
    control.throttle = throttle
    control.brake = brake
    control.hand_brake = hand_brake
    control.reverse = reverse
    client.send_control(control)

def create_controller_output_dir(output_folder):
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

def store_trajectory_plot(graph, fname):
    """ Store the resulting plot.
    """
    create_controller_output_dir(CONTROLLER_OUTPUT_FOLDER)

    file_name = os.path.join(CONTROLLER_OUTPUT_FOLDER, fname)
    graph.savefig(file_name)

def write_trajectory_file(x_list, y_list, v_list, t_list, collided_list):
    create_controller_output_dir(CONTROLLER_OUTPUT_FOLDER)
    file_name = os.path.join(CONTROLLER_OUTPUT_FOLDER, 'trajectory.txt')

    with open(file_name, 'w') as trajectory_file: 
        for i in range(len(x_list)):
            trajectory_file.write('%3.3f, %3.3f, %2.3f, %6.3f %r\n' %\
                                  (x_list[i], y_list[i], v_list[i], t_list[i],
                                   collided_list[i]))

def write_collisioncount_file(collided_list):
    create_controller_output_dir(CONTROLLER_OUTPUT_FOLDER)
    file_name = os.path.join(CONTROLLER_OUTPUT_FOLDER, 'collision_count.txt')

    with open(file_name, 'w') as collision_file: 
        collision_file.write(str(sum(collided_list)))

def make_correction(waypoint,previuos_waypoint,desired_speed):
    dx = waypoint[0] - previuos_waypoint[0]
    dy = waypoint[1] - previuos_waypoint[1]

    if dx < 0:
        moveY = -1.5
    elif dx > 0:
        moveY = 1.5
    else:
        moveY = 0

    if dy < 0:
        moveX = 1.5
    elif dy > 0:
        moveX = -1.5
    else:
        moveX = 0
    
    waypoint_on_lane = waypoint
    waypoint_on_lane[0] += moveX
    waypoint_on_lane[1] += moveY
    waypoint_on_lane[2] = desired_speed

    return waypoint_on_lane
def exec_waypoint_nav_demo(args):
    """ Executes waypoint navigation demo.
    """
    with make_carla_client(args.host, args.port) as client:
        print('Carla client connected.')

        settings = make_carla_settings(args)

        # Now we load these settings into the server. The server replies
        # with a scene description containing the available start spots for
        # the player. Here we can provide a CarlaSettings object or a
        # CarlaSettings.ini file as string.
        scene = client.load_settings(settings)

        # Refer to the player start folder in the WorldOutliner to see the
        # player start information
        player_start = PLAYER_START_INDEX

        # Notify the server that we want to start the episode at the
        # player_start index. This function blocks until the server is ready
        # to start the episode.
        print('Starting new episode at %r...' % scene.map_name)
        client.start_episode(player_start)

        #############################################
        # Load Configurations
        #############################################

        # Load configuration file (options.cfg) and then parses for the various
        # options. Here we have two main options:
        # live_plotting and live_plotting_period, which controls whether
        # live plotting is enabled or how often the live plotter updates
        # during the simulation run.
        config = configparser.ConfigParser()
        config.read(os.path.join(
                os.path.dirname(os.path.realpath(__file__)), 'options.cfg'))
        demo_opt = config['Demo Parameters']

        # Get options
        enable_live_plot = demo_opt.get('live_plotting', 'true').capitalize()
        enable_live_plot = enable_live_plot == 'True'
        live_plot_period = float(demo_opt.get('live_plotting_period', 0))

        # Set options
        live_plot_timer = Timer(live_plot_period)

        # Settings Mission Planner
        mission_planner = CityTrack("Town01")

        #############################################
        # Determine simulation average timestep (and total frames)
        #############################################
        # Ensure at least one frame is used to compute average timestep
        num_iterations = ITER_FOR_SIM_TIMESTEP
        if (ITER_FOR_SIM_TIMESTEP < 1):
            num_iterations = 1

        # Gather current data from the CARLA server. This is used to get the
        # simulator starting game time. Note that we also need to
        # send a command back to the CARLA server because synchronous mode
        # is enabled.
        measurement_data, sensor_data = client.read_data()
        sim_start_stamp = measurement_data.game_timestamp / 1000.0
        # Send a control command to proceed to next iteration.
        # This mainly applies for simulations that are in synchronous mode.
        send_control_command(client, throttle=0.0, steer=0, brake=1.0)
        # Computes the average timestep based on several initial iterations
        sim_duration = 0
        for i in range(num_iterations):
            # Gather current data
            measurement_data, sensor_data = client.read_data()
            # Send a control command to proceed to next iteration
            send_control_command(client, throttle=0.0, steer=0, brake=1.0)
            # Last stamp
            if i == num_iterations - 1:
                sim_duration = measurement_data.game_timestamp / 1000.0 -\
                               sim_start_stamp

        # Outputs average simulation timestep and computes how many frames
        # will elapse before the simulation should end based on various
        # parameters that we set in the beginning.
        SIMULATION_TIME_STEP = sim_duration / float(num_iterations)
        print("SERVER SIMULATION STEP APPROXIMATION: " + \
              str(SIMULATION_TIME_STEP))
        TOTAL_EPISODE_FRAMES = int((TOTAL_RUN_TIME + WAIT_TIME_BEFORE_START) /\
                               SIMULATION_TIME_STEP) + TOTAL_FRAME_BUFFER

        #############################################
        # Frame-by-Frame Iteration and Initialization
        #############################################
        # Store pose history starting from the start position
        measurement_data, sensor_data = client.read_data()
        start_timestamp = measurement_data.game_timestamp / 1000.0
        start_x, start_y, start_z, start_pitch, start_roll, start_yaw = get_current_pose(measurement_data)
        send_control_command(client, throttle=0.0, steer=0, brake=1.0)
        x_history     = [start_x]
        y_history     = [start_y]
        yaw_history   = [start_yaw]
        time_history  = [0]
        speed_history = [0]
        collided_flag_history = [False]  # assume player starts off non-collided

        #############################################
        # Settings Waypoints
        #############################################
        starting    = scene.player_start_spots[PLAYER_START_INDEX]
        destination = scene.player_start_spots[DESTINATION_INDEX]

        # Starting position is the current position
        # (x, y, z, pitch, roll, yaw)
        source_pos = [starting.location.x, starting.location.y, starting.location.z]
        source_ori = [starting.orientation.x, starting.orientation.y]
        source = mission_planner.project_node(source_pos)

        # Destination position
        destination_pos = [destination.location.x, destination.location.y, destination.location.z]
        destination_ori = [destination.orientation.x, destination.orientation.y]
        destination = mission_planner.project_node(destination_pos)

        waypoints = []

        waypoints_native=mission_planner.compute_route(source, source_ori, destination, destination_ori)
        intersection_nodes = mission_planner.get_intersection_nodes()

        for i in range(len(waypoints_native)):
            node=waypoints_native[i]
            waypoints_native[i]=mission_planner._map.convert_to_world(node)

        waypoints_route = mission_planner.compute_route(source, source_ori, destination, destination_ori)
        desired_speed = 5.0
        turn_speed    = 2.5

        intersection_nodes = mission_planner.get_intersection_nodes()

        intersection_nodes_world=[mission_planner._map.convert_to_world(node) for node in intersection_nodes]
        for i in range(len(intersection_nodes_world)):
            intersection_nodes_world[i] = intersection_nodes_world[i][:2]

        #build a square for each waypoints that correspond to the road intersections
        intersection_rectangles=[]
        offset=7
        for i in range(len(waypoints_native)):
            wp = waypoints_native[i]
            for inters in intersection_nodes_world:
                inters_x = inters[0]
                inters_y = inters[1]
                if wp[0] == inters_x and wp[1] == inters_y:
                    xmin,xmax,ymin,ymax=wp[0]-offset,wp[0]+offset,wp[1]-offset,wp[1]+offset
                    rectangle=[xmin,xmax,ymin,ymax]
                    intersection_rectangles.append(rectangle)


        intersection_pair = []
        turn_cooldown = 0
        prev_x = False
        prev_y = False
        # Put waypoints in the lane
        previuos_waypoint = mission_planner._map.convert_to_world(waypoints_route[0])
        for i in range(1,len(waypoints_route)):
            point = waypoints_route[i]

            waypoint = mission_planner._map.convert_to_world(point)

            current_waypoint = make_correction(waypoint,previuos_waypoint,desired_speed)

            dx = current_waypoint[0] - previuos_waypoint[0]
            dy = current_waypoint[1] - previuos_waypoint[1]

            is_turn = ((prev_x and abs(dy) > 0.1) or (prev_y and abs(dx) > 0.1)) and not(abs(dx) > 0.1 and abs(dy) > 0.1)

            prev_x = abs(dx) > 0.1
            prev_y = abs(dy) > 0.1

            if point in intersection_nodes:
                prev_start_intersection = mission_planner._map.convert_to_world(waypoints_route[i-2])
                center_intersection = mission_planner._map.convert_to_world(waypoints_route[i])

                start_intersection = mission_planner._map.convert_to_world(waypoints_route[i-1])
                end_intersection = mission_planner._map.convert_to_world(waypoints_route[i+1])

                start_intersection = make_correction(start_intersection,prev_start_intersection,turn_speed)
                end_intersection = make_correction(end_intersection,center_intersection,turn_speed)

                dx = start_intersection[0] - end_intersection[0]
                dy = start_intersection[1] - end_intersection[1]

                if abs(dx) > 0 and abs(dy) > 0:
                    intersection_pair.append((center_intersection,len(waypoints)))
                    waypoints[-1][2] = turn_speed

                    middle_point = [(start_intersection[0] + end_intersection[0]) /2,  (start_intersection[1] + end_intersection[1]) /2]

                    turn_angle = math.atan2((end_intersection[1] - start_intersection[1]),(start_intersection[0] - end_intersection[0]))
                    #print(turn_angle,  pi / 4, middle_point[0] - center_intersection[0] < 0)

                    turn_adjust = 0 < turn_angle < pi / 2 and middle_point[0] - center_intersection[0] < 0
                    turn_adjust_2 =  pi / 2 < turn_angle < pi and middle_point[0] - center_intersection[0] < 0

                    quater_part = - pi / 2 < turn_angle < 0
                    neg_turn_adjust = quater_part and middle_point[0] - center_intersection[0] < 0
                    neg_turn_adjust_2 = - pi < turn_angle < -pi/2 and middle_point[0] - center_intersection[0] < 0

                    centering = 0.55 if turn_adjust or neg_turn_adjust else 0.75

                    middle_intersection = [(centering*middle_point[0] + (1-centering)*center_intersection[0]),  (centering*middle_point[1] + (1-centering)*center_intersection[1])]

                    # Point at intersection:
                    A = [[start_intersection[0], start_intersection[1], 1],
                         [end_intersection[0], end_intersection[1], 1],
                         [middle_intersection[0], middle_intersection[1], 1]]

                    b = [-start_intersection[0]**2 - start_intersection[1]**2,
                         -end_intersection[0]**2 - end_intersection[1]**2,
                         -middle_intersection[0]**2 - middle_intersection[1]**2]

                    coeffs = np.matmul(np.linalg.inv(A), b)

                    x = start_intersection[0]

                    internal_turn = 0 if turn_adjust or turn_adjust_2 or quater_part  else 1

                    center_x = -coeffs[0]/2 + internal_turn * 0.10
                    center_y = -coeffs[1]/2 + internal_turn * 0.10

                    r = sqrt(center_x**2 + center_y**2 - coeffs[2])

                    theta_start = math.atan2((start_intersection[1] - center_y),(start_intersection[0] - center_x))
                    theta_end = math.atan2((end_intersection[1] - center_y),(end_intersection[0] - center_x))

                    start_to_end = 1 if theta_start < theta_end else -1

                    theta_step = (abs(theta_end - theta_start) * start_to_end) /20

                    theta = theta_start + 6*theta_step

                    while (start_to_end==1 and theta < theta_end - 3*theta_step) or (start_to_end==-1 and theta > theta_end - 6*theta_step):
                        waypoint_on_lane = [0,0,0]

                        waypoint_on_lane[0] = center_x + r * cos(theta)
                        waypoint_on_lane[1] = center_y + r * sin(theta)
                        waypoint_on_lane[1] = center_y + r * sin(theta)
                        waypoint_on_lane[2] = turn_speed

                        waypoints.append(waypoint_on_lane)
                        theta += theta_step

                    turn_cooldown = 4
            else:
                waypoint = mission_planner._map.convert_to_world(point)

                if turn_cooldown > 0:
                    target_speed = turn_speed
                    turn_cooldown -= 1
                else:
                    target_speed = desired_speed

                waypoint_on_lane = make_correction(waypoint,previuos_waypoint,target_speed)

                waypoints.append(waypoint_on_lane)

                previuos_waypoint = waypoint


        waypoints = np.array(waypoints)

        #############################################
        # Controller 2D Class Declaration
        #############################################
        # This is where we take the controller2d.py class
        # and apply it to the simulator
        controller = controller2d.Controller2D(waypoints)

        #############################################
        # Vehicle Trajectory Live Plotting Setup
        #############################################
        # Uses the live plotter to generate live feedback during the simulation
        # The two feedback includes the trajectory feedback and
        # the controller feedback (which includes the speed tracking).
        lp_traj = lv.LivePlotter(tk_title="Trajectory Trace")
        lp_1d = lv.LivePlotter(tk_title="Controls Feedback")

        ###
        # Add 2D position / trajectory plot
        ###
        trajectory_fig = lp_traj.plot_new_dynamic_2d_figure(
                title='Vehicle Trajectory',
                figsize=(FIGSIZE_X_INCHES, FIGSIZE_Y_INCHES),
                edgecolor="black",
                rect=[PLOT_LEFT, PLOT_BOT, PLOT_WIDTH, PLOT_HEIGHT])

        trajectory_fig.set_invert_x_axis() # Because UE4 uses left-handed
                                           # coordinate system the X
                                           # axis in the graph is flipped
        trajectory_fig.set_axis_equal()    # X-Y spacing should be equal in size

        # Add waypoint markers
        trajectory_fig.add_graph("waypoints", window_size=len(waypoints),
                                 x0=waypoints[:,0], y0=waypoints[:,1],
                                 linestyle="-", marker="", color='g')
        # Add trajectory markers
        trajectory_fig.add_graph("trajectory", window_size=TOTAL_EPISODE_FRAMES,
                                 x0=[start_x]*TOTAL_EPISODE_FRAMES,
                                 y0=[start_y]*TOTAL_EPISODE_FRAMES,
                                 color=[1, 0.5, 0])
        # Add starting position marker
        trajectory_fig.add_graph("start_pos", window_size=1,
                                 x0=[start_x], y0=[start_y],
                                 marker=11, color=[1, 0.5, 0],
                                 markertext="Start", marker_text_offset=1)

        trajectory_fig.add_graph("obstacles_points",
                                 window_size=8 * (NUM_PEDESTRIANS + NUM_VEHICLES) ,
                                 x0=[0]* (8 * (NUM_PEDESTRIANS + NUM_VEHICLES)),
                                 y0=[0]* (8 * (NUM_PEDESTRIANS + NUM_VEHICLES)),
                                    linestyle="", marker="+", color='b')

        # Add end position marker
        trajectory_fig.add_graph("end_pos", window_size=1,
                                 x0=[waypoints[-1, 0]],
                                 y0=[waypoints[-1, 1]],
                                 marker="D", color='r',
                                 markertext="End", marker_text_offset=1)
        # Add car marker
        trajectory_fig.add_graph("car", window_size=1,
                                 marker="s", color='b', markertext="Car",
                                 marker_text_offset=1)
        # Add lead car information
        trajectory_fig.add_graph("leadcar", window_size=1,
                                 marker="s", color='g', markertext="Lead Car",
                                 marker_text_offset=1)

        # Add lookahead path
        trajectory_fig.add_graph("selected_path",
                                 window_size=INTERP_MAX_POINTS_PLOT,
                                 x0=[start_x]*INTERP_MAX_POINTS_PLOT,
                                 y0=[start_y]*INTERP_MAX_POINTS_PLOT,
                                 color=[1, 0.5, 0.0],
                                 linewidth=3)

        # Add local path proposals
        for i in range(NUM_PATHS):
            trajectory_fig.add_graph("local_path " + str(i), window_size=200,
                                     x0=None, y0=None, color=[0.0, 0.0, 1.0])

        ###
        # Add 1D speed profile updater
        ###
        forward_speed_fig =\
                lp_1d.plot_new_dynamic_figure(title="Forward Speed (m/s)")
        forward_speed_fig.add_graph("forward_speed",
                                    label="forward_speed",
                                    window_size=TOTAL_EPISODE_FRAMES)
        forward_speed_fig.add_graph("reference_signal",
                                    label="reference_Signal",
                                    window_size=TOTAL_EPISODE_FRAMES)

        # Add throttle signals graph
        throttle_fig = lp_1d.plot_new_dynamic_figure(title="Throttle")
        throttle_fig.add_graph("throttle",
                              label="throttle",
                              window_size=TOTAL_EPISODE_FRAMES)
        # Add brake signals graph
        brake_fig = lp_1d.plot_new_dynamic_figure(title="Brake")
        brake_fig.add_graph("brake",
                              label="brake",
                              window_size=TOTAL_EPISODE_FRAMES)
        # Add steering signals graph
        steer_fig = lp_1d.plot_new_dynamic_figure(title="Steer")
        steer_fig.add_graph("steer",
                              label="steer",
                              window_size=TOTAL_EPISODE_FRAMES)

        # live plotter is disabled, hide windows
        if not enable_live_plot:
            lp_traj._root.withdraw()
            lp_1d._root.withdraw()


        #############################################
        # Local Planner Variables
        #############################################
        wp_goal_index   = 0
        local_waypoints = None
        path_validity   = np.zeros((NUM_PATHS, 1), dtype=bool)
        lp = local_planner.LocalPlanner(NUM_PATHS,
                                        PATH_OFFSET,
                                        CIRCLE_OFFSETS,
                                        CIRCLE_RADII,
                                        PATH_SELECT_WEIGHT,
                                        TIME_GAP,
                                        A_MAX,
                                        SLOW_SPEED,
                                        STOP_LINE_BUFFER)

        bp = behavioural_planner.BehaviouralPlanner(BP_LOOKAHEAD_BASE,
                                                    LEAD_VEHICLE_LOOKAHEAD)

        #############################################
        # Scenario Execution Loop
        #############################################

        # Iterate the frames until the end of the waypoints is reached or
        # the TOTAL_EPISODE_FRAMES is reached. The controller simulation then
        # ouptuts the results to the controller output directory.
        reached_the_end = False
        skip_first_frame = True

        # Initialize the current timestamp.
        current_timestamp = start_timestamp

        # Initialize collision history
        prev_collision_vehicles    = 0
        prev_collision_pedestrians = 0
        prev_collision_other       = 0

        # Initialize collision prediction
        predict_collision = False

        for frame in range(TOTAL_EPISODE_FRAMES):

            # Gather current data from the CARLA server
            measurement_data, sensor_data = client.read_data()

            # Update pose and timestamp
            prev_timestamp = current_timestamp
            current_x, current_y, current_z, current_pitch, current_roll, current_yaw = \
                get_current_pose(measurement_data)
            current_speed = measurement_data.player_measurements.forward_speed
            current_timestamp = float(measurement_data.game_timestamp) / 1000.0

            # Wait for some initial time before starting the demo
            if current_timestamp <= WAIT_TIME_BEFORE_START:
                send_control_command(client, throttle=0.0, steer=0, brake=1.0)
                continue
            else:
                current_timestamp = current_timestamp - WAIT_TIME_BEFORE_START

            # Store history
            x_history.append(current_x)
            y_history.append(current_y)
            yaw_history.append(current_yaw)
            speed_history.append(current_speed)
            time_history.append(current_timestamp)

            # Store collision history
            collided_flag,\
            prev_collision_vehicles,\
            prev_collision_pedestrians,\
            prev_collision_other = get_player_collided_flag(measurement_data,
                                                 prev_collision_vehicles,
                                                 prev_collision_pedestrians,
                                                 prev_collision_other)
            collided_flag_history.append(collided_flag)

            # Execute the behaviour and local planning in the current instance
            # Note that updating the local path during every controller update
            # produces issues with the tracking performance (imagine everytime
            # the controller tried to follow the path, a new path appears). For
            # this reason, the local planner (LP) will update every X frame,
            # stored in the variable LP_FREQUENCY_DIVISOR, as it is analogous
            # to be operating at a frequency that is a division to the
            # simulation frequency.
            if frame % LP_FREQUENCY_DIVISOR == 0:

                #retreive the camera data from Carla sensor_data
                depth_data = sensor_data.get('DepthCamera', None)
                segmentation_data = sensor_data.get('SegmentationCamera', None)
                # Compute open loop speed estimate.
                open_loop_speed = lp._velocity_planner.get_open_loop_speed(current_timestamp - prev_timestamp)

                # Calculate the goal state set in the local frame for the local planner.
                # Current speed should be open loop for the velocity profile generation.
                ego_state = [current_x, current_y, current_yaw, open_loop_speed]
                print(f"EGO STATE -> X : {ego_state[0]} | Y : {ego_state[1]} | YAW : {ego_state[2]} SPEED : {ego_state[3]} ")


                # Set lookahead based on current speed.

                bp.set_lookahead(BP_LOOKAHEAD_BASE + BP_LOOKAHEAD_TIME * open_loop_speed)

                #compute depth and state of traffic light
                tl_depth=MAX_DEPTH
                tl_state, tl_box = check_for_traffic_light(sensor_data=sensor_data)
                if tl_state !=2 and segmentation_data is not None:
                    tl_depth=compute_depth_tl(segmentation_data,depth_data,tl_box)
                #perform the BEHAVIOURAL PLANNER state transition
                bp.transition_state(waypoints, ego_state,tl_depth,tl_state)

                # Update the obstacles list and check to see if we need to follow the lead vehicle.
                obstacles,pedestrians_info,pedestrians,cars,lead_car_state=update_obstacles(bp,measurement_data,current_x,current_y,ego_state)

                # Compute the goal state set from the behavioural planner's computed goal state.
                goal_state_set = lp.get_goal_state_set(bp._goal_index, bp._goal_state, waypoints, ego_state)

                # Calculate planned paths in the local frame.
                paths, path_validity = lp.plan_paths(goal_state_set)

                # Transform those paths back to the global frame.
                paths = local_planner.transform_paths(paths, ego_state)

                # Perform  pedestrian collision checking.
                pedestrian_collision_check_array=lp._collision_checker.collision_check_pedestrian(paths, pedestrians)
                bp._obstacle,is_active_collision=predict_pedestrian_collisions(pedestrian_collision_check_array,pedestrians_info,ego_state,DELTA_ORIENTATION)

                # check if the ego_vehicle is in an intersection
                in_intersection=manage_intersection(intersection_rectangles, ego_state,measurement_data)
                if in_intersection:
                    bp.set_lookahead(30)

                #Perform  cars collision checking.
                collision_check_array = lp._collision_checker.collision_check(paths, cars)
                cars_collision = np.array(collision_check_array)

                # Compute the best local path.
                best_index = lp._collision_checker.select_best_path_index(paths, collision_check_array, bp._goal_state)
                # If no path was feasible, continue to follow the previous best path.
                if best_index == None:
                    best_path = lp._prev_best_path
                else:
                    best_path = paths[best_index]
                    lp._prev_best_path = best_path

                #predict the collisions between the ego_vehicle and the other cars in an intersection
                check_collision_intersections(bp,cars_collision,in_intersection,percentage=0.7)

                #Perform  pedestrian emergency brake if needed
                emergency_break_pedestrian(ego_state, x_history, y_history, measurement_data, pedestrians_info,bp)

                if best_path is not None:
                    # Compute the velocity profile for the path, and compute the waypoints.
                    desired_speed = bp._goal_state[2]
                    decelerate_to_tl = bp._state == behavioural_planner.TRAFFICLIGHT_STOP
                    follow_lead_vehicle=bp._follow_lead_vehicle
                    emergency_break=bp._handbrake

                    local_waypoints = lp._velocity_planner.compute_velocity_profile(best_path, desired_speed, ego_state, current_speed, decelerate_to_tl, lead_car_state,follow_lead_vehicle,emergency_break)


                    if local_waypoints != None:
                        # Update the controller waypoint path with the best local path.
                        # This controller is similar to that developed in Course 1 of this
                        # specialization.  Linear interpolation computation on the waypoints
                        # is also used to ensure a fine resolution between points.
                        wp_distance = []   # distance array
                        local_waypoints_np = np.array(local_waypoints)
                        for i in range(1, local_waypoints_np.shape[0]):
                            wp_distance.append(
                                    np.sqrt((local_waypoints_np[i, 0] - local_waypoints_np[i-1, 0])**2 +
                                            (local_waypoints_np[i, 1] - local_waypoints_np[i-1, 1])**2))
                        wp_distance.append(0)  # last distance is 0 because it is the distance
                                            # from the last waypoint to the last waypoint

                        # Linearly interpolate between waypoints and store in a list
                        wp_interp      = []    # interpolated values
                                            # (rows = waypoints, columns = [x, y, v])
                        for i in range(local_waypoints_np.shape[0] - 1):
                            # Add original waypoint to interpolated waypoints list (and append
                            # it to the hash table)
                            wp_interp.append(list(local_waypoints_np[i]))

                            # Interpolate to the next waypoint. First compute the number of
                            # points to interpolate based on the desired resolution and
                            # incrementally add interpolated points until the next waypoint
                            # is about to be reached.
                            num_pts_to_interp = int(np.floor(wp_distance[i] /\
                                                        float(INTERP_DISTANCE_RES)) - 1)
                            wp_vector = local_waypoints_np[i+1] - local_waypoints_np[i]
                            wp_uvector = wp_vector / np.linalg.norm(wp_vector[0:2])

                            for j in range(num_pts_to_interp):
                                next_wp_vector = INTERP_DISTANCE_RES * float(j+1) * wp_uvector
                                wp_interp.append(list(local_waypoints_np[i] + next_wp_vector))
                        # add last waypoint at the end
                        wp_interp.append(list(local_waypoints_np[-1]))

                        # Update the other controller values and controls
                        controller.update_waypoints(wp_interp)

                print(f"handbrake : {bp._handbrake}")
                print(f"in intersection : {in_intersection}")
                print(f"pedestrian obstacle : {bp._obstacle}")
                print(f"lead vehicle : {bp._follow_lead_vehicle}")
                print('----------------')
            ###
            # Controller Update
            ###
            if local_waypoints != None and local_waypoints != []:
                controller.update_values(current_x, current_y, current_yaw,
                                         current_speed,
                                         current_timestamp, frame)
                controller.update_controls()
                cmd_throttle, cmd_steer, cmd_brake = controller.get_commands()

            else:
                cmd_throttle = 0.0
                cmd_steer = 0.0
                cmd_brake = 0.0

            # perform emergency brake
            if bp._handbrake:
                cmd_throttle=0.0
                cmd_brake = 1

            # Skip the first frame or if there exists no local paths
            if skip_first_frame and frame == 0:
                pass
            elif local_waypoints == None:
                pass
            else:
                # Update live plotter with new feedback
                trajectory_fig.roll("trajectory", current_x, current_y)
                trajectory_fig.roll("car", current_x, current_y)

                # Load obstacles points
                if obstacles.size != 0:
                    x = obstacles[:,0]
                    y = obstacles[:,1]
                    for i in range(len(x)):
                        trajectory_fig.roll("obstacles_points", x[i], y[i])

                if lead_car_state != []:    # If there exists a lead car, plot it
                    trajectory_fig.roll("leadcar", lead_car_state[0], lead_car_state[1])
                else :
                    trajectory_fig.roll("leadcar", 0, 0)

                forward_speed_fig.roll("forward_speed",
                                       current_timestamp,
                                       current_speed)
                forward_speed_fig.roll("reference_signal",
                                       current_timestamp,
                                       controller._desired_speed)
                throttle_fig.roll("throttle", current_timestamp, cmd_throttle)
                brake_fig.roll("brake", current_timestamp, cmd_brake)
                steer_fig.roll("steer", current_timestamp, cmd_steer)

                # Local path plotter update
                if frame % LP_FREQUENCY_DIVISOR == 0:
                    path_counter = 0
                    try:
                        for i in range(NUM_PATHS):
                            # If a path was invalid in the set, there is no path to plot.
                            if path_validity[i]:
                                # Colour paths according to collision checking.
                                if is_active_collision[i]:
                                    if not pedestrian_collision_check_array[path_counter]:
                                        colour = 'r'
                                    elif i == best_index:
                                        colour = 'k'
                                    else:
                                        colour = 'b'
                                if pedestrian_collision_check_array[path_counter]:
                                    if not collision_check_array[path_counter]:
                                        colour = 'r'
                                    elif i == best_index:
                                        colour = 'k'
                                    else:
                                        colour = 'b'

                                trajectory_fig.update("local_path " + str(i), paths[path_counter][0], paths[path_counter][1], colour)
                                path_counter += 1
                            else:
                                trajectory_fig.update("local_path " + str(i), [ego_state[0]], [ego_state[1]], 'r')
                    except:
                        pass
                # When plotting lookahead path, only plot a number of points
                # (INTERP_MAX_POINTS_PLOT amount of points). This is meant
                # to decrease load when live plotting

                wp_interp_np = np.array(wp_interp)
                path_indices = np.floor(np.linspace(0,
                                                    wp_interp_np.shape[0]-1,
                                                    INTERP_MAX_POINTS_PLOT))
                trajectory_fig.update("selected_path",
                        wp_interp_np[path_indices.astype(int), 0],
                        wp_interp_np[path_indices.astype(int), 1],
                        new_colour=[1, 0.5, 0.0])


                # Refresh the live plot based on the refresh rate
                # set by the options
                if enable_live_plot and \
                   live_plot_timer.has_exceeded_lap_period():
                    lp_traj.refresh()
                    lp_1d.refresh()
                    live_plot_timer.lap()


            # Output controller command to CARLA server
            send_control_command(client,
                                 throttle=cmd_throttle,
                                 steer=cmd_steer,
                                 brake=cmd_brake)


            # Find if reached the end of waypoint. If the car is within
            # DIST_THRESHOLD_TO_LAST_WAYPOINT to the last waypoint,
            # the simulation will end.
            dist_to_last_waypoint = np.linalg.norm(np.array([
                waypoints[-1][0] - current_x,
                waypoints[-1][1] - current_y]))
            if  dist_to_last_waypoint < DIST_THRESHOLD_TO_LAST_WAYPOINT:
                reached_the_end = True
            if reached_the_end:
                break

        # End of demo - Stop vehicle and Store outputs to the controller output
        # directory.
        if reached_the_end:
            print("Reached the end of path. Writing to controller_output...")
        else:
            print("Exceeded assessment time. Writing to controller_output...")
        # Stop the car
        send_control_command(client, throttle=0.0, steer=0.0, brake=1.0)
        # Store the various outputs
        store_trajectory_plot(trajectory_fig.fig, 'trajectory.png')
        store_trajectory_plot(forward_speed_fig.fig, 'forward_speed.png')
        store_trajectory_plot(throttle_fig.fig, 'throttle_output.png')
        store_trajectory_plot(brake_fig.fig, 'brake_output.png')
        store_trajectory_plot(steer_fig.fig, 'steer_output.png')
        write_trajectory_file(x_history, y_history, speed_history, time_history,
                              collided_flag_history)
        write_collisioncount_file(collided_flag_history)

def main():
    """Main function.

    Args:
        -v, --verbose: print debug information
        --host: IP of the host server (default: localhost)
        -p, --port: TCP port to listen to (default: 2000)
        -a, --autopilot: enable autopilot
        -q, --quality-level: graphics quality level [Low or Epic]
        -i, --images-to-disk: save images to disk
        -c, --carla-settings: Path to CarlaSettings.ini file
    """
    argparser = argparse.ArgumentParser(description=__doc__)
    argparser.add_argument(
        '-v', '--verbose',
        action='store_true',
        dest='debug',
        help='print debug information')
    argparser.add_argument(
        '--host',
        metavar='H',
        default='localhost',
        help='IP of the host server (default: localhost)')
    argparser.add_argument(
        '-p', '--port',
        metavar='P',
        default=2000,
        type=int,
        help='TCP port to listen to (default: 2000)')
    argparser.add_argument(
        '-a', '--autopilot',
        action='store_true',
        help='enable autopilot')
    argparser.add_argument(
        '-q', '--quality-level',
        choices=['Low', 'Epic'],
        type=lambda s: s.title(),
        default='Low',
        help='graphics quality level.')
    argparser.add_argument(
        '-c', '--carla-settings',
        metavar='PATH',
        dest='settings_filepath',
        default=None,
        help='Path to a "CarlaSettings.ini" file')
    args = argparser.parse_args()

    # Logging startup info
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(format='%(levelname)s: %(message)s', level=log_level)
    logging.info('listening to server %s:%s', args.host, args.port)

    args.out_filename_format = '_out/episode_{:0>4d}/{:s}/{:0>6d}'

    # Execute when server connection is established
    while True:
        try:
            exec_waypoint_nav_demo(args)
            print('Done.')
            return

        except TCPConnectionError as error:
            logging.error(error)
            time.sleep(1)

if __name__ == '__main__':

    try:
        main()
    except KeyboardInterrupt:
        print('\nCancelled by user. Bye!')

