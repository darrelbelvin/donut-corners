import matplotlib.pyplot as plt
import numpy as np
import plotly.express as px
from donut_corners import DonutCorners

def show_img(img, cmap=None):
    plt.figure()
    plt.imshow(img, cmap=cmap)
    mng = plt.get_current_fig_manager()
    mng.window.showMaximized()
    plt.show()


def show_imgs(imgs):
    if len(imgs) == 1:
        show_img(imgs[0])
        return
    
    fig, axs = plt.subplots(ncols=len(imgs))
    for i, ax in enumerate(axs):
        ax.imshow(imgs[i])
    
    mng = plt.get_current_fig_manager()
    mng.window.showMaximized()
    plt.show()


def get_2dimg(dc, imgtype = 'slopes'):
    img = {'slopes': dc.slopes, 'interest': dc.interest * 255}[imgtype]
    return np.pad(img,((0,0),(0,0),(1,0)), mode='constant')


def paint_basins(img, dc: DonutCorners):
    n=1
    s1 = np.pad(dc.basins[:,:-n], ((0,0),(n,0)), mode='constant', constant_values = -1)
    s2 = np.pad(dc.basins[:-n,:], ((n,0),(0,0)), mode='constant', constant_values = -1)

    add_img = ((dc.basins != s1) | (dc.basins != s2)).astype(int)
    
    add_img = np.pad(add_img[:,:,None], ((0,0),(0,0),(2,0)), mode='edge')

    return np.max(np.array([img, add_img * 255]), axis=0)


def paint_corners(img, dc: DonutCorners):
    add_img = np.zeros_like(img, dtype=float)

    for point in dc.corners:
        add_img[point[1][0], point[1][1], :] = point[0]

        # if dc.eval_method['sectional']:
        #     score, angles, beam_strengths, beam_ids = dc.score_point(point[1])
        #     for angle, strength in zip(angles, beam_strengths):
        #         #paint rays
        #         pass
    
    if np.max(add_img) != 0:
        add_img = (add_img / np.max(add_img) * 255).astype(int)
    
    img[add_img != 0] = add_img[add_img != 0]
    return img
    return np.max(np.array([img, add_img]), axis=0)


def show_beam(dc: DonutCorners):
    show_3d_kernel(dc.spiral)


def show_3d_kernel(arr):
    points = np.array(list(np.ndindex(arr.shape)))[arr.flatten() != 0]
    fig = px.scatter_3d(x=points[:,1], y=points[:,2], z=points[:,0], color=arr[points[:,0], points[:,1], points[:,2]], opacity=0.5)
    fig.show()