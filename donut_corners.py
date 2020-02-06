import cv2
import numpy as np
from scipy import optimize
from scipy.optimize._minimize import _minimize_neldermead

from collections import deque

from multiprocessing import Pool, cpu_count
from math import pi
import random

import sys
sys.path.append('..')
from simplex_descent_quantized.simplex_descent_quantized import simplex_descent_quantized

class DonutCorners():
    rot90 = np.array([[0, -1], [1, 0]])
    
    # pylint: disable=too-many-instance-attributes
    def __init__(self, **kwargs):
        # passed on params
        self.simplex_args = dict(max_iters = 1000, max_step = 4, initial_simplex_size = 3)
        self.search_args = dict(top_n=10, img_shape=None)
        self.img_shape = None
        self.top_n = None

        # beam & lighthouse
        self.angle_count = 12 # must be multiple of 4
        self.beam_count = self.angle_count * 3
        self.beam_width = 3
        self.fork_spread = 2
        self.beam_length = 30
        self.beam_start = 0
        self.beam_round = True
        self.beam_width = 2

        self.eval_method = {'sectional': False, 'elimination_width': self.beam_count // 30, 'max_n': 3, 'elim_double_ends': False}

        # grid params
        self.grid_size = 30
        self.min_corner_score = 0
        
        self.__dict__.update(kwargs)

        self.beam_diameter = 1 + self.beam_length * 2

        self.scored = None
        self.scored_partial = None
        self.point_info = None
        self.basins = None
        self.corners = None

        self.baked_angles = np.linspace(0, 2*pi, self.angle_count, endpoint=False)
        self.beam()


    def init(self, image):
        if isinstance(image, str):
            self.src = cv2.imread(image)
        else:
            self.src = image
        
        self.dims = np.array(self.src.shape[:2], dtype=int)
        self.scored_partial = np.empty(self.dims)
        self.scored_partial[:] = np.NaN
        self.point_info = {}
        self.basins = np.zeros(self.dims, dtype=int)
        self.corners = []

        self.preprocess()


    def preprocess(self):
        if len(self.src.shape) == 3:
            self.bw = np.mean(self.src, axis=-1)
        else:
            self.bw = self.src
        self.bw = np.pad(self.bw, (
            (self.beam_length,self.beam_length),(self.beam_length,self.beam_length)),
             mode='edge').astype('float32')
    

    def fit(self, X, y):
        return self
    

    def transform(self, img_list, img_shape=None):
        if self.search_args["img_shape"] is None:
            if img_shape:
                self.search_args["img_shape"] = img_shape
            else:
                raise ValueError("I need an image shape!")
        
        w = img_list.shape[1]
        with_features = np.empty((img_list.shape[0], w + self.search_args["top_n"] * 7))
        with_features[:, :w] = img_list
        with_features[:, w:] = np.nan

        for i, img in enumerate(img_list):
            self.init(img.reshape(self.search_args["img_shape"]))
            top = self.find_corners_grid(**self.search_args)
            top = np.hstack([np.hstack((c[0], (c[1][0],), c[1][1], c[1][2])).flatten() for c in top])
            with_features[i,w:w + len(top)] = top
            print(f'{i/img_list.shape[0]:.2%}', end='\r')

        means = np.nanmean(with_features, axis=0)
        inds = np.where(np.isnan(with_features))
        with_features[inds] = np.take(means, inds[1])

        return with_features


    def set_params(self, **kwargs):
        self.__dict__.update(kwargs)


    def beam(self):
        r, d, ir = self.beam_length, self.beam_diameter, self.beam_start
        w, spr, count = self.beam_width, self.fork_spread, self.beam_count

        ind = np.array(list(np.ndindex((d,d)))).reshape((d,d,2))
        delta = ind - r

        beam_angles = np.linspace(0,2*pi, count, endpoint=False)
        
        beam_uvs = np.stack((np.sin(beam_angles), np.cos(beam_angles)), axis=-1)
        beam_perps = np.matmul(beam_uvs, DonutCorners.rot90)

        len_on_line = np.array([delta.dot(uv) for uv in beam_uvs])
        dist_to_line = np.array([delta.dot(perp) for perp in beam_perps])
        
        # make the prongs
        prong1 = np.maximum(w / 2 - np.abs(dist_to_line - spr / 2), 0)
        prong2 = np.minimum(-w / 2 + np.abs(dist_to_line + spr / 2), 0)

        # clip to length & side
        prong1[(len_on_line < ir) | (len_on_line > r) | (dist_to_line <= 0)] = 0
        prong2[(len_on_line < ir) | (len_on_line > r) | (dist_to_line > 0)] = 0

        # normalize
        c_1, c_2 = np.sum(prong1 != 0, axis=(1,2)), np.abs(np.sum(prong2 != 0, axis=(1,2)))
        c_big = np.max(np.array((c_1, c_2)), axis=0)
        prong1 = prong1 / np.sum(prong1, axis=(1,2))[:, None, None]
        prong2 = prong2 / np.sum(prong2, axis=(1,2))[:, None, None]

        # combine
        spiral = prong1 - prong2

        # store
        self.spiral = spiral.astype('float32')
        self.spiral_mask = spiral != 0
        self.weights = [self.spiral[i,...][self.spiral_mask[i,...]] for i in range(count)]
        self.beam_index = np.argwhere(self.spiral_mask)[...,0]
        self.beam_jumps = np.argwhere(self.beam_index[1:] != self.beam_index[:-1]).flatten() + 1


    # scoring methods
    def get_score(self, point, inform=False):
        point = np.array(point, dtype=int)

        if self.out_of_bounds(point):
            return 0
        
        if inform:
            tp = tuple(point)
            if tp in self.point_info:
                return self.point_info[tp][0], self.point_info[tp], True

            info = self.score_point(point)
            self.point_info[tp] = info
            self.scored_partial[point[0],point[1]] = info[0]
            return info[0], info, False

        if self.scored is not None:
            return self.scored[point[0],point[1]]
        
        if np.isnan(self.scored_partial[point[0],point[1]]):
            self.scored_partial[point[0],point[1]] = self.score_point(point)[0]
        
        return self.scored_partial[point[0],point[1]]


    def score_point(self, point):
        region = self.bw[point[0] : point[0] + self.beam_diameter,
                         point[1] : point[1] + self.beam_diameter]
        
        interest = [region[beam] for beam in self.spiral_mask]
        means = np.array([np.abs(np.mean(w * i)) for w, i in zip(self.weights, interest)])

        if not self.eval_method['sectional']:
            return (np.mean(means),)

        maxs = np.array([DonutCorners.get_max_idx(means, w=self.eval_method['elimination_width'],
                no_doubles = self.eval_method['elim_double_ends']) for _ in range(self.eval_method['max_n'])])

        beam_strengths = maxs[:,1]
        beam_ids = maxs[:,0].astype(int)
        angles = self.baked_angles[beam_ids]

        return np.mean(beam_strengths), angles, beam_strengths, beam_ids
    

    @staticmethod
    def get_max_idx(vals, w = 1, no_doubles = True, gradual = False):
        arg = np.argmax(vals)
        val = vals[arg]
        ind = np.arange(arg-w, arg + w + 1) % len(vals)
        vals[ind] = 0

        if gradual:
            if no_doubles:
                pass

        elif no_doubles:
            vals[(ind + len(vals)//2) % len(vals)] = 0 #eliminate double counting of edges
        
        return [arg, val]
        

    def score_row(self, y):
        return [self.score_point([y,x])[0] for x in range(self.src.shape[1])]


    def score_all(self, multithread = True):
        
        if multithread:
            with Pool(cpu_count() - 1) as p:
                out = p.map(self.score_row, range(self.src.shape[0]))
        
        else:
            out = [self.score_row(y) for y in range(self.src.shape[0])]
        
        out = np.array(out)
        
        self.scored = out
        return out
    

    def find_corner(self, point):

        def callback(*args, **kwargs):
            print('Callback')
            print(args)
            print(kwargs)

        negative = lambda *args: -1 * self.get_score(*args)
        result = simplex_descent_quantized(negative, 1, np.array(point, dtype=int), 
                basin_mapping = True, current_basin_map = self.basins,
                bounds = np.stack([np.zeros_like(self.dims), self.dims], axis=1), **self.simplex_args)

        # res = np.array([-result['y'], result['x'][0], result['x'][1]])
        # assert not np.any([np.all(self.corners == c) for c in self.corners])

        if result['exit_cause'] == "maximum found":
            res = np.array([-result['y'], result['x'][0], result['x'][1]])
            if not np.any([np.all(res == c) for c in self.corners]):
                self.corners.append(res)
                return res
        
        return None


    def find_corners(self, multithread = False, edge_offset = 5, top_n=10, stop_percent=None, max_rounds=None, **kwargs):
        i = 0
        while np.min(self.basins[edge_offset:-edge_offset,edge_offset:-edge_offset]) == 0:
            true_ind = np.argwhere(self.basins == 0)
            chosen = np.random.randint(0, true_ind.shape[0])
            self.find_corner(true_ind[chosen])
            if stop_percent is not None and np.mean(self.basins != 0) > stop_percent:
                break
            if max_rounds is not None and i >= max_rounds:
                break
            i += 1
        
        strengths = [a[0] for a in self.corners]
        top = np.argsort(strengths)[-1:-top_n-1:-1] # make strongest first
        return [self.corners[i] for i in top]
    

    def out_of_bounds(self, point):
        return not np.all((point >= 0) & (point < self.src.shape[:-1]))
        

    def search_rays(self, point, angles, dists, info):
        best_v, best_p, best_i, best_info = info[0], point, -1, info
        for angle in angles:
            for new_i, dist in enumerate(dists):
                new_p = point + np.round(dist*np.array((np.sin(angle),np.cos(angle)))).astype(int)
                assert not np.all(new_p == point)
                if self.out_of_bounds(new_p):
                    continue
                new_v, new_info, exist = self.get_score(new_p, True)
                assert new_info is not None
                # if exist:
                #     # this point has already been part of a search, stop searching neighbors
                #     return
                if new_v > best_v:
                    best_v, best_p, best_i, best_info = new_v, new_p, new_i, new_info
        if best_i == -1:
            mode = -1
        elif best_i == 0 or best_i == len(angles) - 1:
            mode = 0
        else:
            mode = 1
        return (mode, best_p, best_info)
    
    
    def find_corners_grid(self, multithread = False, min_grid=0.1, top_n=10, **kwargs):
        #from queue import Queue
        #q = Queue()
        q = deque()

        std_rays = np.swapaxes(np.mgrid[-1:2,-1:2], 0,2)
        std_rays = np.delete(std_rays, (8,9)).reshape(-1,2)
        
        if 'single_point' in kwargs:
            q.append((1, kwargs['single_point'], None))

        else:
            grid = np.mgrid[self.grid_size//2:self.dims[0]:self.grid_size,
                    self.grid_size//2:self.dims[1]:self.grid_size]
            # grid_size = grid.shape[:2]
            grid_points = np.swapaxes(grid, 0,2).reshape(-1,2)

            for point in grid_points:
                q.append((1, point, None))
        
        brute_angles = np.math.pi * np.arange(8) / 4
        mode_points_tried = set()

        def add(data):
            tp = (data[0],) + tuple(data[1])
            if tp not in mode_points_tried:
                mode_points_tried.add(tp)
                q.append(data)

        print(" x".ljust(8)," y".ljust(8), "queue".rjust(8))

        while True:
            mode, point, info = q.popleft()
            # info = score, angles, beam_strengths, beam_ids

            if mode == 1: # initial grid point
                val, info, _ = self.get_score(point, True)
                if val > min_grid:
                    add((2, point, info))

            else:
                if mode == 2: # following rays long dist
                    dists = np.array((-0.7, -0.5, -0.3, 0.3 ,0.5 ,0.7))*self.beam_length
                    angles = info[1]
                    
                elif mode == 3: # following rays short dist
                    dists = (-5.6, -2.8, -1.4, 1.4 ,2.8 ,5.6)
                    angles = info[1]

                elif mode == 4: # brute force immideate area
                    dists = (1.4,)
                    angles = brute_angles
                
                elif mode == 5: # check super long dist rays for other corners
                    dists = np.array((-2,-1.5,-1, 1, 1.5, 2))*self.beam_length
                    angles = info[1]
            
                mode_add, point2, info2 = self.search_rays(point, angles, dists, info)

                if mode_add == -1 and mode == 4: # found a local max
                    self.corners.append((info2[0], point2, info2))
                    
                    info2 = (info2[0]*0.5,) + info2[1:] # don't disqualify points slightly weaker than this in edge following
                    add((5, point2, info2))

                elif mode == 5: # looking for potential other corners
                    if mode_add != -1: # found one
                        add((2, point2, info2))
  
                #elif tuple(point2) not in self.point_info: # don't search it again if we've already been here
                else:
                    mode += abs(mode_add)
                    add((mode, point2, info2))

            print(str(point[0]).ljust(8),str(point[1]).ljust(8), str(len(q)).rjust(8), end='\r')
            if len(q) == 0:
                break

            # if q.qsize() == 0:
            #     q.join()
            #     if q.qsize() == 0:
            #         break


        strengths = [a[0] for a in self.corners]
        top = np.argsort(strengths)[-1:-top_n-1:-1] # make strongest first
        return [self.corners[i] for i in top]


if __name__ == "__main__":
    from visualizing_donut_corners import *
    #img = cv2.imread('images/bldg-2.jpg')
    img = cv2.imread('images/legos_examples/4.jpg')
    #img = cv2.imread('images/tex-1.JPG')
    #crop
    #img = img[:200, 650:950]
    #img = img[500:1500:5, 500:1500:5]

    kwargs = {'angle_count': 12 * 4,
            'beam_count': 12 * 4,
            'beam_width': 2,
            'fork_spread': 1,
            'beam_length': 20,
            'beam_start': 5,
            'beam_round': True,
            'eval_method': {'sectional': True, 'elimination_width': 6, 'max_n': 3, 'elim_double_ends': True}
            }

    dc = DonutCorners(**kwargs)
    dc.init(img)
    print(dc.get_score(np.array([50,50])))

    #show_beam(dc)
    
    #print(dc.score_point(np.array([50,50])))
    import sys
    #dc.score_all('pydevd' not in sys.modules)
    
    #print(dc.find_corner(np.array([50,70])))
    dc.find_corners_grid(min_grid=0.1)
    
    # for _ in range(10):
    #     point = np.random.randint(0,200,size=2)
    #     dc.find_corners_grid(single_point = point, min_grid=0.1)

    #print(dc.find_corners_grid(single_point = np.array([150,70]), min_grid=0.1))
    #dc.find_corners_grid()
    #print(dc.find_corners())#'pydevd' not in sys.modules)

    if dc.scored is not None:
        sc = dc.scored
    else:
        sc = np.nan_to_num(dc.scored_partial, nan=-0.5*np.max(np.nan_to_num(dc.scored_partial)))
    sc = sc / np.max(sc) * 255
    sc = np.pad(sc[...,None], ((0,0),(0,0),(0,2)), mode='constant').astype(int)

    # show_img(paint_corners(np.maximum(dc.src[...,[2,1,0]], sc), dc))
    # show_img(sc)
    show_imgs((dc.src[...,[2,1,0]], paint_corners(sc + (dc.src[...,[2,1,0]]/10).astype(int), dc)))


    #show_std(dc)
    
    print('done')
    print('leaving')