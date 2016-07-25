from IPython import embed as shell
from core.internal_repr.predicate import Predicate
from core.util_classes.common_predicates import ExprPredicate
from core.util_classes.matrix import Vector3d, PR2PoseVector
from errors_exceptions import PredicateException
from core.util_classes.openrave_body import OpenRAVEBody
from core.util_classes.pr2 import PR2
from sco.expr import Expr, AffExpr, EqExpr, LEqExpr
from collections import OrderedDict
import numpy as np
import ctrajoptpy

"""
This file implements the classes for commonly used predicates that are useful in a wide variety of
typical domains.
"""
BASE_MOVE = 1e0
JOINT_MOVE = np.pi/8

class CollisionPredicate(ExprPredicate):
    def __init__(self, name, e, attr_inds, params, expected_param_types, dsafe = 0.05, debug = False, ind0=0, ind1=1):
        self._debug = debug
        if self._debug:
            self._env.SetViewer("qtcoin")
        self._cc = ctrajoptpy.GetCollisionChecker(self._env)
        self.dsafe = dsafe
        self.ind0 = ind0
        self.ind1 = ind1
        super(CollisionPredicate, self).__init__(name, e, attr_inds, params, expected_param_types)

    def distance_from_obj(self, x):
        # self._cc.SetContactDistance(self.dsafe + .1)
        self._cc.SetContactDistance(np.Inf)
        p0 = self.params[self.ind0]
        p1 = self.params[self.ind1]
        b0 = self._param_to_body[p0]
        b1 = self._param_to_body[p1]
        pose0 = x[0:3]
        pose1 = x[3:6]
        b0.set_pose(pose0)
        b1.set_pose(pose1)

        collisions = self._cc.BodyVsBody(b0.env_body, b1.env_body)

        col_val, jac0, jac1 = self._calc_grad_and_val(p0.name, p1.name, pose0, pose1, collisions)
        val = np.array([col_val])
        jac = np.r_[jac0, jac1].reshape((1, 6))
        return val, jac

    def _calc_grad_and_val(self, name0, name1, pose0, pose1, collisions):
        val = -1 * float("inf")
        jac0 = None
        jac1 = None
        for c in collisions:
            linkA = c.GetLinkAParentName()
            linkB = c.GetLinkBParentName()

            if linkA == name0 and linkB == name1:
                pt0 = c.GetPtA()
                pt1 = c.GetPtB()
            elif linkB == name0 and linkA == name1:
                pt0 = c.GetPtB()
                pt1 = c.GetPtA()
            else:
                continue

            distance = c.GetDistance()
            normal = c.GetNormal()

            # plotting
            if self._debug:
                pt0[2] = 1.01
                pt1[2] = 1.01
                self._plot_collision(pt0, pt1, distance)
                print "pt0 = ", pt0
                print "pt1 = ", pt1
                print "distance = ", distance

            # if there are multiple collisions, use the one with the greatest penetration distance
            if self.dsafe - distance > val:
                val = self.dsafe - distance
                jac0 = -1 * normal[0:2]
                jac1 = normal[0:2]

        return val, jac0, jac1

    def pose_rot_check(self, x):
        # Assuming x is aligned according to the following order:
        # BasePose->BackHeight->LeftArmPose->LeftGripper->RightArmPose->RightGripper->CanPose->CanRot

        # Setting pose for each ravebody
        robot_body = self._param_to_body[self.params[self.ind0]]
        obj_body = self._param_to_body[self.params[self.ind1]]
        base_pose, back_height = x[0:3], x[3]
        l_arm_pose, l_gripper = x[4:11], x[11]
        r_arm_pose, r_gripper = x[12:19], x[19]
        can_pose, can_rotation = x[20:23], x[23:]
        robot = robot_body.env_body
        robot_body.set_pose(base_pose)
        robot_body.set_dof(back_height, l_arm_pose, l_gripper, r_arm_pose, r_gripper)
        obj_body.set_pose(can_pose, can_rotation)
        # Helper variables that will be used in many places
        tool_link = robot.GetLink("r_gripper_tool_frame")
        rarm_inds = robot.GetManipulator('rightarm').GetArmIndices()
        rarm_joints = [robot.GetJointFromDOFIndex(ind) for ind in rarm_inds]
        Rz, Ry, Rx = OpenRAVEBody._axis_rot_matrices(can_pose, can_rotation)
        # axises = [axis_x, axis_y, axis_z]
        axises = [np.dot(Rz, np.dot(Ry, [1,0,0])), np.dot(Rz, [0,1,0]), [0,0,1]]
        # Two function calls return the value and jacobian of each constraints
        pos_val, pos_jac = self.pos_error(obj_body, tool_link, axises, rarm_joints)
        rot_val, rot_jac = self.rot_error(obj_body, tool_link, axises, rarm_joints)

        return pos_val, pos_jac, rot_val, rot_jac

    def pos_error(self, obj_body, tool_link, axises, arm_joints):
        # Calculate the value and the jacobian regarding displacement between center of gripper and center of can
        gp = np.array([0,0,0])
        robot_pos = tool_link.GetTransform()[:3, 3]
        obj_trans = obj_body.env_body.GetTransform()
        obj_pos = np.dot(obj_trans, np.r_[gp, 1])[:3]
        dist_val = robot_pos.flatten() - obj_pos.flatten()
        # Calculate the joint jacobian
        arm_jac = np.array([np.cross(joint.GetAxis(), robot_pos.flatten() - joint.GetAnchor()) for joint in arm_joints]).T.copy()
        # Calculate jacobian for the robot base
        base_jac = np.eye(3)
        base_jac[:,2] = np.cross(np.array([0, 0, 1]), robot_pos - self.x[:3])
        # Calculate jacobian for the back hight
        torso_jac = np.array([[0],[0],[1]])
        # Calculate object jacobian
        obj_jac = -1*np.array([np.cross(axis, obj_pos - gp - obj_trans[:3,3].flatten()) for axis in axises]).T
        obj_jac = np.c_[-np.eye(3), obj_jac]
        # Create final 3x26 jacobian matrix -> (Gradient checked to be correct)
        dist_jac = np.hstack((base_jac, torso_jac, np.zeros((3, 8)), arm_jac, np.zeros((3, 1)), obj_jac))

        return (dist_val, dist_jac)

    def rot_error(self, obj_body, tool_link, axises, arm_joints):
        # Calculate object transformation, and direction vectors
        obj_trans = obj_body.env_body.GetTransform()
        local_dir = np.array([0.,0.,1.])
        obj_dir = np.dot(obj_trans[:3,:3], local_dir)
        world_dir = tool_link.GetTransform()[:3,:3].dot(local_dir)
        rot_val = np.dot(obj_dir, world_dir) - 1
        # computing robot's jacobian
        arm_jac = np.array([np.dot(obj_dir, np.cross(joint.GetAxis(), world_dir)) for joint in arm_joints]).T.copy()
        arm_jac = arm_jac.reshape((1, len(arm_joints)))
        base_jac = np.array(np.dot(obj_dir, np.cross([0,0,1], world_dir)))
        base_jac = np.array([[0, 0, base_jac]])
        # computing object's jacobian
        obj_jac = np.array([np.dot(world_dir, np.cross(axis, obj_dir)) for axis in axises])
        obj_jac = np.r_[obj_jac, [0,0,0]].reshape((1, 6))
        # Create final 1x26 jacobian matrix
        rot_jac = np.hstack((base_jac, np.zeros((1, 9)), arm_jac, np.zeros((1,1)), obj_jac))

        return (rot_val, rot_jac)

    def face_up(self, tool_link, arm_joints): # Not used
        # calculate the value and jacobian regarding direction of which the gripper is facing
        local_dir = np.array([0.,0.,1.])
        face_val = tool_link.GetTransform()[:2,:3].dot(local_dir)
        world_dir = tool_link.GetTransform()[:3,:3].dot(local_dir)
        arm_jac = np.array([np.cross(joint.GetAxis(), world_dir)[:2] for joint in arm_joints]).T.copy()
        face_jac = np.hstack((np.zeros((2, 12)), arm_jac, np.zeros((2, 1)), np.zeros((2, 6))))

        return (face_val, face_jac)

    def grasp_facing(self, x): # returns an expression of gripper's facing
        # calculate the value and jacobian regarding direction of which the gripper is facing
        robot_body = self._param_to_body[self.params[self.ind0]]
        obj_body = self._param_to_body[self.params[self.ind1]]
        base_pose, back_height = x[0:3], x[3]
        l_arm_pose, l_gripper = x[4:11], x[11]
        r_arm_pose, r_gripper = x[12:19], x[19]
        can_pose, can_rotation = x[20:23], x[23:]
        robot = robot_body.env_body
        robot_body.set_pose(base_pose)
        robot_body.set_dof(back_height, l_arm_pose, l_gripper, r_arm_pose, r_gripper)
        obj_body.set_pose(can_pose, can_rotation)
        # Helper variables that will be used in many places
        tool_link = robot.GetLink("r_gripper_tool_frame")
        rarm_inds = robot.GetManipulator('rightarm').GetArmIndices()
        rarm_joints = [robot.GetJointFromDOFIndex(ind) for ind in rarm_inds]
        Rz, Ry, Rx = OpenRAVEBody._axis_rot_matrices(can_pose, can_rotation)
        # axises = [axis_x, axis_y, axis_z]
        axises = [np.dot(Rz, np.dot(Ry, [1,0,0])), np.dot(Rz, [0,1,0]), [0,0,1]]

        # three dimensional vector specifying gripper's facing
        face_dir = tool_link.GetTransform()[:3,:3]
        arm_jac = np.array([np.dot(obj_dir, np.cross(joint.GetAxis(), face_dir)) for joint in arm_joints]).T.copy()
        base_jac = np.eye(3)
        base_jac[:,2] = np.cross(np.array([0, 0, 1]), robot_pos - self.x[:3])
        face_jac = np.hstack((base_jac, np.zeros((1, 9)), arm_jac, np.zeros((1,7))))
        face_val = face_dir.dot(self.grasp.value) - 1
        import ipdb; ipdb.set_trace()

        return (face_val, face_jac)

    def _plot_collision(self, ptA, ptB, distance):
        self.handles = []
        if not np.allclose(ptA, ptB, atol=1e-3):
            if distance < 0:
                self.handles.append(self._env.drawarrow(p1=ptA, p2=ptB, linewidth=.01, color=(1, 0, 0)))
            else:
                self.handles.append(self._env.drawarrow(p1=ptA, p2=ptB, linewidth=.01, color=(0, 0, 0)))

class At(ExprPredicate):

    # At, Can, Target

    def __init__(self, name, params, expected_param_types, env=None):
        assert len(params) == 2
        self.can, self.target = params
        attr_inds = OrderedDict([(self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))]),
                                 (self.target, [("value", np.array([0,1,2], dtype=np.int)),
                                                  ("rotation", np.array([0,1,2], dtype=np.int))])])

        A = np.c_[np.eye(6), -np.eye(6)]
        b, val = np.zeros((6, 1)), np.zeros((6, 1))
        aff_e = AffExpr(A, b)
        e = EqExpr(aff_e, val)
        super(At, self).__init__(name, e, attr_inds, params, expected_param_types)

class RobotAt(ExprPredicate):

    # RobotAt, Robot, RobotPose

    def __init__(self, name, params, expected_param_types, env=None):
        assert len(params) == 2
        self.r, self.rp = params
        attr_inds = OrderedDict([(self.r, [("pose", np.array([0,1,2], dtype=np.int)),
                                            ("backHeight", np.array([0], dtype=np.int)),
                                            ("lArmPose", np.array(range(7), dtype=np.int)),
                                            ("lGripper", np.array([0], dtype=np.int)),
                                            ("rArmPose", np.array(range(7), dtype=np.int)),
                                            ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.rp, [("value", np.array([0,1,2], dtype=np.int)),
                                             ("backHeight", np.array([0], dtype=np.int)),
                                             ("lArmPose", np.array(range(7), dtype=np.int)),
                                             ("lGripper", np.array([0], dtype=np.int)),
                                             ("rArmPose", np.array(range(7), dtype=np.int)),
                                             ("rGripper", np.array([0], dtype=np.int))])])

        A = np.c_[np.eye(20), -np.eye(20)]
        b ,val = np.zeros((20, 1)), np.zeros((20, 1))
        aff_e = AffExpr(A, b)
        e = EqExpr(aff_e, val)
        super(RobotAt, self).__init__(name, e, attr_inds, params, expected_param_types)

class IsGP(CollisionPredicate):

    # IsGP, Robot, RobotPose, Can
    # This predicate only checks whether can pose is at center of gripper

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 3
        self._env = env
        self.robot, self.robot_pose, self.can = params
        attr_inds = OrderedDict([(self.robot_pose, [("value", np.array([0, 1, 2], dtype=np.int)),
                                                   ("backHeight", np.array([0], dtype=np.int)),
                                                   ("lArmPose", np.array(range(7), dtype=np.int)),
                                                   ("lGripper", np.array([0], dtype=np.int)),
                                                   ("rArmPose", np.array(range(7), dtype=np.int)),
                                                   ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot_pose: self.lazy_spawn_or_body(self.robot_pose, self.robot_pose.name, self.robot.geom),
                               self.can: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[0]
        grad = lambda x: self.pose_rot_check(x)[1]

        pos_expr = Expr(f, grad)
        e = EqExpr(pos_expr, np.zeros((3, 1)))
        super(IsGP, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=1, ind1=2)

class IsGPRot(CollisionPredicate):

    # IsGP, Robot, RobotPose, Can
    # This predicate checks whether can has the same rotation axis as that of gripper

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 3
        self._env = env
        self.robot, self.robot_pose, self.can = params
        attr_inds = OrderedDict([(self.robot_pose, [("value", np.array([0, 1, 2], dtype=np.int)),
                                                   ("backHeight", np.array([0], dtype=np.int)),
                                                   ("lArmPose", np.array(range(7), dtype=np.int)),
                                                   ("lGripper", np.array([0], dtype=np.int)),
                                                   ("rArmPose", np.array(range(7), dtype=np.int)),
                                                   ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot_pose: self.lazy_spawn_or_body(self.robot_pose, self.robot_pose.name, self.robot.geom),
                               self.can: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[2]
        grad = lambda x: self.pose_rot_check(x)[3]

        pos_expr = Expr(f, grad)
        e = EqExpr(pos_expr, np.zeros((2, 1)))
        super(IsGPRot, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=1, ind1=2)

class IsPDP(CollisionPredicate):

    # IsPDP, Robot, RobotPose, Can, Target
    # This predicate only checks whether can pose is at center of gripper

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 4
        self._env = env
        self.robot, self.robot_pose, self.can, self.target = params
        attr_inds = OrderedDict([(self.robot_pose, [("value", np.array([0, 1, 2], dtype=np.int)),
                                                   ("backHeight", np.array([0], dtype=np.int)),
                                                   ("lArmPose", np.array(range(7), dtype=np.int)),
                                                   ("lGripper", np.array([0], dtype=np.int)),
                                                   ("rArmPose", np.array(range(7), dtype=np.int)),
                                                   ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.target, [("value", np.array([0,1,2], dtype=np.int)),
                                                  ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot_pose: self.lazy_spawn_or_body(self.robot_pose, self.robot_pose.name, self.robot.geom),
                               self.target: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[0]
        grad = lambda x: self.pose_rot_check(x)[1]

        face_expr = Expr(f, grad)
        e = EqExpr(face_expr, np.zeros((3, 1)))
        super(IsPDP, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=1, ind1=3)

class IsPDPRot(CollisionPredicate):

    # IsPDP, Robot, RobotPose, Can, target
    # This predicate checks whether can has the same rotation axis as that of gripper

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 4
        self._env = env
        self.robot, self.robot_pose, self.can, self.target = params
        attr_inds = OrderedDict([(self.robot_pose, [("value", np.array([0, 1, 2], dtype=np.int)),
                                                   ("backHeight", np.array([0], dtype=np.int)),
                                                   ("lArmPose", np.array(range(7), dtype=np.int)),
                                                   ("lGripper", np.array([0], dtype=np.int)),
                                                   ("rArmPose", np.array(range(7), dtype=np.int)),
                                                   ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.target, [("value", np.array([0,1,2], dtype=np.int)),
                                                  ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot_pose: self.lazy_spawn_or_body(self.robot_pose, self.robot_pose.name, self.robot.geom),
                               self.target: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[2]
        grad = lambda x: self.pose_rot_check(x)[3]

        face_expr = Expr(f, grad)
        e = EqExpr(face_expr, np.zeros((2, 1)))
        super(IsPDPRot, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=1, ind1=3)

class InGripper(CollisionPredicate):

    # InGripper, Robot, Can

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 2
        self._env = env
        self.robot, self.can = params
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                               ("backHeight", np.array([0], dtype=np.int)),
                                               ("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot: self.lazy_spawn_or_body(self.robot, self.robot.name, self.robot.geom),
                               self.can: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[0]
        grad = lambda x: self.pose_rot_check(x)[1]

        pos_expr = Expr(f, grad)
        e = EqExpr(pos_expr, np.zeros((3,1)))
        super(InGripper, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=0, ind1=1)

class InGripperRot(CollisionPredicate):

    # InGripper, Robot, Can

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 2
        self._env = env
        self.robot, self.can = params
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                               ("backHeight", np.array([0], dtype=np.int)),
                                               ("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot: self.lazy_spawn_or_body(self.robot, self.robot.name, self.robot.geom),
                               self.can: self.lazy_spawn_or_body(self.can, self.can.name, self.can.geom)}

        f = lambda x: self.pose_rot_check(x)[2]
        grad = lambda x: self.pose_rot_check(x)[3]

        pos_expr = Expr(f, grad)
        e = EqExpr(pos_expr, np.zeros((1,1)))
        super(InGripperRot, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=0, ind1=1)

class InGripperAngle(CollisionPredicate):

    # InGripperAngle, Robot, Grasp

    def __init__(self, name, params, expected_param_types, env = None, debug = False):
        assert len(params) == 2
        self._env = env
        self.robot, self.grasp = params
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                               ("backHeight", np.array([0], dtype=np.int)),
                                               ("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.grasp, [("value", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {self.robot: self.lazy_spawn_or_body(self.robot, self.robot.name, self.robot.geom)}

        f = lambda x: self.grasp_facing(x)[0]
        grad = lambda x: self.grasp_facing(x)[1]

        pos_expr = Expr(f, grad)
        e = EqExpr(pos_expr, np.zeros((1, 1)))
        super(InGripperAngle, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=0, ind1=1)

class IsMP(ExprPredicate):

    # IsMP Robot

    def __init__(self, name, params, expected_param_types, env=None, debug=False):
        self.robot, = params
        ## constraints  |x_t - x_{t+1}| < dmove
        ## ==> x_t - x_{t+1} < dmove, -x_t + x_{t+a} < dmove
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                               ("backHeight", np.array([0], dtype=np.int)),
                                               ("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))])])
        A = np.eye(40) - np.eye(40, k=20) - np.eye(40, k=-20)
        b = np.zeros((40,))
        val = np.vstack((BASE_MOVE*np.ones((3,1)), JOINT_MOVE*np.ones((17,1)), BASE_MOVE*np.ones((3,1)), JOINT_MOVE*np.ones((17,1)))).reshape((40,))
        e = LEqExpr(AffExpr(A, b), val)

        super(IsMP, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class Stationary(ExprPredicate):

    # Stationary, Can

    def __init__(self, name, params, expected_param_types, env=None):
        assert len(params) == 1
        self.can,  = params
        attr_inds = OrderedDict([(self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        A = np.c_[np.eye(6), -np.eye(6)]
        b, val = np.zeros((6, 1)), np.zeros((6, 1))
        e = EqExpr(AffExpr(A, b), val)
        super(Stationary, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class StationaryBase(ExprPredicate):

    # StationaryBase, Robot (Only Robot Base)

    def __init__(self, name, params, expected_param_types, env=None):
        assert len(params) == 1
        self.robot,  = params
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int))])])

        A = np.c_[np.eye(3), -np.eye(3)]
        b, val = np.zeros((3, 1)), np.zeros((3, 1))
        e = EqExpr(AffExpr(A, b), val)
        super(StationaryBase, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class StationaryArm(ExprPredicate):

    # StationaryArm, Robot (Only Robot Arms)

    def __init__(self, name, params, expected_param_types, env=None):
        assert len(params) == 1
        self.robot,  = params
        attr_inds = OrderedDict([(self.robot, [("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))])])

        A = np.c_[np.eye(16), -np.eye(16)]
        b, val = np.zeros((16, 1)), np.zeros((16, 1))
        e = EqExpr(AffExpr(A, b), val)
        super(StationaryArm, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class StationaryW(ExprPredicate):

    # StationaryW, Obstacle

    def __init__(self, name, params, expected_param_types, env=None, debug=False):
        self.w, = params
        attr_inds = OrderedDict([(self.w, [("pose", np.array([0, 1], dtype=np.int))])])
        A = np.c_[np.eye(2), -np.eye(2)]
        b = np.zeros((2, 1))
        e = EqExpr(AffExpr(A, b), b)
        super(StationaryW, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class StationaryNEQ(ExprPredicate):

    # StationaryNEq, Can, Can(Hold)

    def __init__(self, name, params, expected_param_types, env=None, debug=False):
        self.can, self.can_held = params
        attr_inds = OrderedDict([(self.can, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                             ("rotation", np.array([0, 1, 2], dtype=np.int))]),
                                 (self.can_held, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                                  ("rotation", np.array([0, 1, 2], dtype=np.int))])])

        if self.c.name == self.c_held.name:
            A = np.zeros((1, 12))
            b = np.zeros((1, 1))
        else:
            A = np.c_[np.eye(6), -np.eye(6)]
            b = np.zeros((2, 1))
        e = EqExpr(AffExpr(A, b), b)
        super(StationaryNEq, self).__init__(name, e, attr_inds, params, expected_param_types, dynamic=True)

class GraspValid(ExprPredicate):
    # GraspValid RobotPose Target Grasp
    pass


class Obstructs(CollisionPredicate): #TODO Not yet ready

    # Obstructs, Robot, RobotPose, Can

    def __init__(self, name, params, expected_param_types, env=None, debug=False):
        assert len(params) == 3
        self._env = env
        self.robot, self.robot_pose, self.can = params
        attr_inds = OrderedDict([(self.robot, [("pose", np.array([0, 1, 2], dtype=np.int)),
                                               ("backHeight", np.array([0], dtype=np.int)),
                                               ("lArmPose", np.array(range(7), dtype=np.int)),
                                               ("lGripper", np.array([0], dtype=np.int)),
                                               ("rArmPose", np.array(range(7), dtype=np.int)),
                                               ("rGripper", np.array([0], dtype=np.int))]),
                                 (self.robot_pose,[]),
                                 (self.can, [("pose", np.array([0,1,2], dtype=np.int)),
                                             ("rotation", np.array([0,1,2], dtype=np.int))])])

        self._param_to_body = {r: self.lazy_spawn_or_body(r, r.name, r.geom),
                               rp: self.lazy_spawn_or_body(rp, rp.name, r.geom),
                               c: self.lazy_spawn_or_body(c, c.name, c.geom)}
        f = lambda x: -self.distance_from_obj(x)[0]
        grad = lambda x: -self.distance_from_obj(x)[1]

        col_expr = Expr(f, grad)
        val = np.zeros((1,1))
        e = LEqExpr(col_expr, val)
        super(Obstructs, self).__init__(name, e, attr_inds, params, expected_param_types, ind0=1, ind1=2)

class ObstructsHolding(CollisionPredicate):
    pass
