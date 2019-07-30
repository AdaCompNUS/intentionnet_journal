import intention_config as config
import time
import pose_utils as pu
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped
from nav_msgs.msg import Path, Odometry
from visualization_msgs.msg import Marker
from rosgraph_msgs.msg import Clock
from std_msgs.msg import Float32MultiArray, String, Float32
import random
import math
import rospy
from functools import reduce
class Localizer(object):
	def __init__(self):
		self.current_pose = None
		# used to estimate the heading direction of the robots
		self.last_pose = None

	def update_pose(self, msg):
		self.last_pose = self.current_pose
		self.current_pose = msg

from navfn.srv import MakeNavPlan
SRV_MAKE_PLAN = '/global_planner/make_plan'
#SRV_MAKE_PLAN = '/move_base/NavfnROS/make_plan'
#from nav_msgs.srv import GetPlan as MakeNavPlan
class Planner(object):
	def __init__(self):
		rospy.wait_for_service(SRV_MAKE_PLAN)
		self.srv_make_plan = rospy.ServiceProxy(SRV_MAKE_PLAN, MakeNavPlan)

	def make_plan(self, start, goal):
		start = pu.toPoseStamped(start)
		goal = pu.toPoseStamped(goal)
		# print (start,goal)
		try:
			#res = self.srv_make_plan(start, goal, config.TOLERANCE)
			res = self.srv_make_plan(start, goal)
				#print 'replanning hahahahaha', res
		except rospy.ServiceException as e:
			print ("make_plan failed: %s" % e)
			return
		if not res.plan_found:
		#if not res.plan:
			print ("make_plan failed: %s" % res.error_message)
			return
		return res.path
	#return res.plan.poses

NUM_INTENTION=config.NUM_INTENTION
AHEAD_DIST = config.AHEAD_DIST
LOCAL_SHIFT = config.LOCAL_SHIFT
TURNING_THRESHOLD = config.TURNING_THRESHOLD
POSE_TOPIC= config.POSE_TOPIC
NAV_GOAL_TOPIC = config.NAV_GOAL_TOPIC
PATH_TOPIC= config.PATH_TOPIC
MAX_X, MAX_Y = config.MAX_X, config.MAX_Y

LAST=time.time()
class IntentionPlanner(object):
	def __init__(self, use_topic_planner=False, default_intention=[0.0]*config.NUM_INTENTION):
		self.use_topic_planner = use_topic_planner
		self.localizer = None
		self.planner = None
		self.planner_topic = None
		# intention
		self.intention = None
		self.default_intention = default_intention
		# previous path
		self.prev_path = None
		# use to index the path
		self.current_idx = 0
		self.ahead_idx = 0

		self.goal_msg = None
		# use to check whether moving
		self.prev_pose_msg = None
		self.skip = 0

		self.pub_cur_pose = rospy.Publisher('/current', Marker, queue_size=1)
		self.pub_goal_pose = rospy.Publisher('/goal', Marker, queue_size=1)
		self.pub_change_goal = rospy.Publisher(NAV_GOAL_TOPIC, PoseStamped, queue_size=1)
		#self.pub_intention = rospy.Publisher('/test_intention', Float32MultiArray, queue_size=1)
		self.pub_intention = rospy.Publisher('/test_intention', String, queue_size=1)
		self.pub_turning = rospy.Publisher('/turning_angle', Float32, queue_size=1)
		print ('initialize done!')

	def run(self):
		# subscribe goal
		rospy.Subscriber(NAV_GOAL_TOPIC, PoseStamped, self.cb_change_goal)
		rospy.Subscriber(POSE_TOPIC, PoseWithCovarianceStamped, self.cb_current_pose)
		#rospy.Subscriber('/clock', Clock, self.cb_clock)

	def change_goal(self):
		def random_quaternion():
			#x = random.uniform(0, 1)
			x = 1.0
			r1 = math.sqrt(1.0-x)
			r2 = math.sqrt(x)
			t1 = math.pi * 2 * random.uniform(0, 1)
			t2 = math.pi * 2 * random.uniform(0, 1)
			c1 = math.cos(t1)
			s1 = math.sin(t1)
			c2 = math.cos(t2)
			s2 = math.sin(t2)
			return [s1*r1, c1*r1, s2*r2, c2*r2]

		x = random.uniform(0, MAX_X)
		y = random.uniform(0, MAX_Y)
		new_goal = PoseStamped()
		new_goal.pose.position.x = x
		new_goal.pose.position.y = y
		quaternion = random_quaternion()
		new_goal.pose.orientation.x=quaternion[0]
		new_goal.pose.orientation.y=quaternion[1]
		new_goal.pose.orientation.z=quaternion[2]
		new_goal.pose.orientation.w=quaternion[3]
		new_goal.header.frame_id = "/map"
		new_goal.header.stamp = rospy.Time.now()
		self.pub_change_goal.publish(new_goal)

	def is_near_goal(self, start, goal):
		if goal and pu.dist(start, goal) < 0.3:
			return True
		return False

	def is_stop(self, start, goal):
		is_near = self.is_near_goal(start, goal)
		if is_near:
			angle_diff = pu.angle_diff(start, goal)
			if angle_diff < 10e-7:
				return True
		return False

	def cb_clock(self, msg):
		#print 'self.skip', self.skip
		if self.skip % 40 == 0:
			self.skip = 0
			is_stop = self.is_stop(self.localizer.current_pose, self.prev_pose_msg)
			if is_stop:
				self.change_goal()
			self.prev_pose_msg = self.localizer.current_pose
		self.skip += 1

	def cb_current_pose(self, msg):
		# print ('current pose', msg)
		self.localizer.update_pose(msg)
		is_goal = self.is_near_goal(msg, self.goal_msg)

		if is_goal:
			#self.change_goal()
			self.pub_intention.publish(config.STOP)
		else:
			self.replan()
		'''
		global LAST
		NOW = time.time()
		print 'time', NOW-LAST
		LAST=NOW
		'''

	def cb_change_goal(self, msg):
	#print 'got goal:', msg
		self.goal_msg = msg
		self.replan()

	def cb_path_planner(self, msg):
		self.prev_path = msg.poses

	def register_planner(self, planner):
		self.planner = planner

	def register_planner_topic(self, topic):
		self.planner_topic = topic
		rospy.Subscriber(self.planner_topic, Path, self.cb_path_planner)

	def register_localizer(self, localizer):
		self.localizer = localizer

	def reset_idx(self):
		self.current_idx = 0
		self.ahead_idx = 0

	def replan(self):
		cur_pose = self.localizer.current_pose
		# print (cur_pose)
		if not cur_pose or not self.goal_msg:
			# print (cur_pose, self.goal_msg)
			# print ("no current pose or goal")
			return

		if not self.use_topic_planner:
			#global LAST
			#LAST = time.time()
			path = self.planner.make_plan(cur_pose, self.goal_msg)
			# print (path)
			#NOW = time.time()
			#print 'time', NOW-LAST
			if not path:
				print ("no path from %s to %s" % (repr(cur_pose), repr(self.goal_msg)))
			else:
				self.prev_path = path
				self.reset_idx()

		#print 'path length', len(self.prev_path)
		self.intention, turning_angle = self.parse_intention(self.prev_path)
		print ('replan intention', self.intention)
		self.pub_intention.publish(self.intention)
		self.pub_turning.publish(turning_angle)
		return self.intention

	# for visualization
	def update_marker(self, pos, pos1=None, is_goal=False):
		marker = Marker()
		if self.use_topic_planner:
			marker.header.frame_id = "/map"
		else:
			marker.header.frame_id = "/map"
		marker.header.stamp = rospy.Time.now()
		marker.type = Marker.ARROW;
			#marker.pose = pu.pose(pos)
		marker.scale.x = 0.2
		marker.scale.y = 1
		if is_goal:
			marker.color.a = 1
			marker.color.r = 1.0
			marker.color.g = 0.1
			marker.color.b = 0.1
		else:
			marker.color.a = 1
			marker.color.r = 0.1
			marker.color.g = 1.0
			marker.color.b = 1.0
		if pos1:
			marker.points.append(pu.pose(pos).position)
			marker.points.append(pu.pose(pos1).position)
		return marker

	def marker_strip(self, poses):
		marker = Marker()
		if self.use_topic_planner:
			marker.header.frame_id = "/map"
		else:
			marker.header.frame_id = "/map"
		marker.header.stamp = rospy.Time.now()
		marker.type = Marker.LINE_STRIP;
		marker.scale.x = 0.7
		marker.scale.y = 1
		marker.color.a = 1
		marker.color.r = 1
		marker.color.g = 0.1
		marker.color.b = 0.1

		for pos in poses:
			marker.points.append(pu.pose(pos).position)
		return marker

	def parse_intention(self, path):
		if path is None:
			#intention = Float32MultiArray()
			#intention.data = self.default_intention
			intention = self.default_intention
			return intention

		def get_valid_next_idx(idx):
			return min(len(path)-1, idx+LOCAL_SHIFT)

		def get_angle(idx):
			p0, p1 = path[idx], path[get_valid_next_idx(idx)]
			a = pu.angle_pose_pair(p0, p1)
			return a

		def get_pair_angle(idx1, idx2):
			p0, p1 = path[idx1], path[idx2]
			a = pu.angle_pose_pair(p0, p1)
			return a

		self.ahead_idx = self.current_idx
		intention = Float32MultiArray()
		'''
		angles = []
		for i in range(NUM_INTENTION/5):
			self.ahead_idx = get_valid_next_idx(self.ahead_idx)
			#angles.append(get_angle(self.ahead_idx))
			angles.append(pu.angle_pose_pair(self.localizer.last_pose, path[self.ahead_idx]))
		current_angle = reduce(lambda x, y: x + y, angles) / len(angles)
		'''
		current_angle = 0
		if self.localizer.last_pose:
			current_angle = pu.angle_pose_pair(self.localizer.last_pose, path[self.current_idx])
		# ignore some beginning position
		for i in range(int(NUM_INTENTION/5)):
			self.ahead_idx = get_valid_next_idx(self.ahead_idx)
		for i in range(NUM_INTENTION):
			self.ahead_idx = get_valid_next_idx(self.ahead_idx)
			intention.data.append(pu.norm_angle(get_pair_angle(self.current_idx, self.ahead_idx) - current_angle))
		'''

		# update ahead idx
		self.ahead_idx = self.current_idx
		dist = lambda: pu.dist(path[self.current_idx], path[self.ahead_idx])
		while self.ahead_idx < len(path)-LOCAL_SHIFT and dist() < AHEAD_DIST:
			self.ahead_idx += 1

		if self.ahead_idx == self.current_idx:
			return self.default_intention

		p0 = path[self.current_idx]
		p01 = path[get_valid_next_idx(self.current_idx)]
		p1 = path[self.ahead_idx]
		p11 = path[get_valid_next_idx(self.ahead_idx)]
		a0 = get_angle(self.current_idx)
		a1 = get_angle(self.ahead_idx)
		dist = pu.dist(p0, p1)
		turning_angle = pu.norm_angle(a1-a0)

	# visualize
		if self.localizer.last_pose:
			self.pub_cur_pose.publish(self.update_marker(self.localizer.last_pose, path[self.current_idx]))
	self.pub_goal_pose.publish(self.update_marker(path[self.current_idx+NUM_INTENTION*4/5], path[self.current_idx+NUM_INTENTION-1], True))
		'''
		self.pub_cur_pose.publish(self.marker_strip(path[self.current_idx : self.current_idx+LOCAL_SHIFT*NUM_INTENTION]))

		turning_angle = reduce(lambda x, y: x + y, intention.data) / len(intention.data)
		#print 'current angle', current_angle
		temp = [t * 180 / 3.14 for t in intention.data]
		#print 'intention data', temp
		#print 'turning angle', turning_angle

		if turning_angle > TURNING_THRESHOLD:
			intention = config.LEFT
		elif turning_angle < -TURNING_THRESHOLD:
			intention = config.RIGHT
		else:
			intention = config.FORWARD

		return intention, turning_angle

def main():
	rospy.init_node("intention_planner")
	if config.WITH_PATH_TOPIC:
		ip = IntentionPlanner(True)
		ip.register_planner_topic(PATH_TOPIC)
	else:
		planner = Planner()
		ip = IntentionPlanner(False, config.STOP)
		ip.register_planner(planner)
	localizer = Localizer()
	ip.register_localizer(localizer)
	ip.run()
	rospy.spin()

if __name__ == '__main__':
	main()