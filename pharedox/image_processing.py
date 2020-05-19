import logging
from typing import Dict, List, Union

import matplotlib as mpl
import matplotlib.pyplot as plt
import numpy as np
import numpy.ma as ma
import pandas as pd
import SimpleITK as sitk
import xarray as xr
from numpy.polynomial.polynomial import Polynomial
from scipy import ndimage as ndi
from scipy.interpolate import UnivariateSpline
from scipy.stats import norm, zscore
from skimage import exposure, filters, img_as_float, measure, transform
from skimage import io
from skimage.measure import label, regionprops
from skimage.transform import AffineTransform, warp

from pharedox import profile_processing


def measure_under_labels(
    I: xr.DataArray,
    S: xr.DataArray,
    ref_wvl: str = "410",
    ratio_numerator="410",
    ratio_denominator="470",
    measurements: List[str] = ["label", "mean_intensity", "area"],
):
    """Measure the intensities of each channel under the label image"""
    df = []
    I = I.where(I.wavelength != "TL", drop=True)

    for a in I.animal.values:
        for tp in I.timepoint.values:
            for p in I.pair.values:
                for wvl in I.wavelength.values:
                    img_selector = dict(animal=a, timepoint=tp, pair=p, wavelength=wvl)
                    seg_selector = dict(
                        animal=a, timepoint=tp, pair=p, wavelength=ref_wvl
                    )

                    if "wavelength" in S.dims:
                        seg_frame = S.sel(
                            animal=a, timepoint=tp, pair=p, wavelength=ref_wvl
                        )
                    else:
                        # single wavelength was passed
                        seg_frame = S.sel(animal=a, timepoint=tp, pair=p)

                    L = measure.label(seg_frame)

                    sub_df = pd.DataFrame(
                        measure.regionprops_table(
                            L,
                            intensity_image=I.sel(**img_selector).values,
                            properties=measurements,
                        )
                    )

                    sub_df["animal"] = a
                    sub_df["timepoint"] = tp
                    sub_df["pair"] = p
                    sub_df["wavelength"] = wvl
                    sub_df["strain"] = I.sel(**img_selector).strain.values

                    df.append(sub_df)

    df = pd.concat(df)
    df = df.set_index(["animal", "timepoint", "pair", "wavelength", "label"]).unstack(
        "wavelength"
    )
    df[("mean_intensity", "r")] = (
        df["mean_intensity"][ratio_numerator] / df["mean_intensity"][ratio_denominator]
    )
    df[("area", "r")] = df[("area", ratio_numerator)]
    df[("strain", "r")] = df[("strain", ratio_numerator)]

    df = df.stack("wavelength")

    return df


def subtract_medians(imgs: xr.DataArray) -> xr.DataArray:
    """
    Subtract the median from each image.

    Parameters
    ----------
    imgs
        the images to subtract the median from. May be a high-dimensional array.
    img_dims
        the dimensions that the images are stored in the `imgs` array. 
    """

    submed = imgs.copy()
    submed.values = np.maximum(imgs - imgs.median(dim=["x", "y"]), 0)
    submed.loc[dict(wavelength="TL")] = imgs.sel(wavelength="TL")
    submed = submed.astype(imgs.dtype)
    return submed


def get_lr_bounds(
    rot_seg_stack: xr.DataArray, pad: int = 0, ref_wvl: str = "410", ref_pair: int = 0
) -> np.ndarray:
    """
    Get the left and right boundaries of the rotated pharynxes

    Parameters
    ----------
    rot_seg_stack
        the rotated segmented pharynxes
    pad
        the amount of padding on the left/right of the  bounds
    ref_wvl
        the wavelength to use for calculating bounds
    ref_pair
        the pair to use for calculating bounds

    Returns
    -------
    bounds
        An (m, 2) array where m = number of animals, the first column is the left bound
        and the second column is the right bound
    """
    imgs = rot_seg_stack.sel(wavelength=ref_wvl, pair=ref_pair)
    bounds = np.zeros((imgs.animal.size, 2))  # (animal, (l, r))
    for i, img in enumerate(imgs):
        _, l, _, r = measure.regionprops(measure.label(img))[0].bbox
        bounds[i, :] = [l - pad, r + pad - 1]
    return bounds.astype(np.int)


def center_and_rotate_pharynxes(
    fl_images: xr.DataArray,
    seg_images: xr.DataArray,
    reference_wavelength: str = "410",
    blur_seg_thresh=1000,
) -> (xr.DataArray, xr.DataArray):
    """
    Given a fluorescence stack and a pharyngeal mask stack, center and rotate each frame of both the FL and mask such
    that the pharynx is in the center of the image, with its anterior on the left.

    Parameters
    ----------
    fl_images
        The fluorescence images to rotate and align
    seg_images
        The segmented images to rotate and align
    reference_wavelength
        The wavelength to use for calculating center of mass and angle of orientation

    Returns
    -------
    (rotated_fl_stack, rotated_seg_stack)
        A 2-tuple where the first item is the rotated fluorescence stack and the second is the rotated mask stack

    Notes
    -----
    This function uses the a reference wavelength to calculate the center of mass and angle of orientation, then applies
    the according translation to all wavelengths for that animal/pair.

    The current implementation uses a gaussian blur on the fluorescence images and segments that to calculate the
    centroid and the orientation angle.
    """
    img_center_y, img_center_x = (
        fl_images.y.size // 2,
        fl_images.x.size // 2,
    )  # (y, x)

    fl_rotated_stack = fl_images.copy()
    seg_rotated_stack = seg_images.copy()

    blurred_seg = fl_images.copy()
    blurred_seg_data = ndi.gaussian_filter(fl_images, sigma=(0, 0, 0, 0, 6, 6))
    blurred_seg_data = blurred_seg_data > blur_seg_thresh
    blurred_seg.data = blurred_seg_data

    # STACK_ITERATION
    for img_idx in range(fl_images.animal.size):
        for wvl in fl_images.wavelength.data:
            for pair in fl_images.pair.data:
                for tp in fl_images.timepoint.values:
                    # Optimization potential here...
                    # this recalculates all region properties for the reference each time
                    reference_seg = seg_images.isel(animal=img_idx).sel(
                        wavelength=reference_wavelength, pair=pair, timepoint=tp
                    )
                    img = fl_images.isel(animal=img_idx).sel(
                        wavelength=wvl, pair=pair, timepoint=tp
                    )
                    seg = seg_rotated_stack.isel(animal=img_idx).sel(
                        wavelength=wvl, pair=pair, timepoint=tp
                    )

                    try:
                        props = measure.regionprops(measure.label(reference_seg))[0]
                    except IndexError:
                        raise ValueError(
                            f"No binary objects found in image @ [idx={img_idx} ; wvl={wvl} ; pair={pair}]"
                        )

                    # pharynx_center_y, pharynx_center_x = props.centroid
                    pharynx_center_y, pharynx_center_x = np.mean(
                        np.nonzero(reference_seg), axis=1
                    )
                    pharynx_orientation = props.orientation

                    translation_matrix = transform.EuclideanTransform(
                        translation=(
                            -(img_center_x - pharynx_center_x),
                            -(img_center_y - pharynx_center_y),
                        )
                    )

                    rotated_img = rotate(
                        img.data, translation_matrix, pharynx_orientation
                    )
                    rotated_seg = rotate(
                        seg.data, translation_matrix, pharynx_orientation, order=0
                    )

                    fl_rotated_stack.loc[dict(wavelength=wvl, pair=pair, timepoint=tp)][
                        img_idx
                    ] = rotated_img

                    seg_rotated_stack.loc[
                        dict(wavelength=wvl, pair=pair, timepoint=tp)
                    ][img_idx] = rotated_seg

    fl_rotated_stack.values = fl_rotated_stack.values.astype(fl_images.dtype)
    return fl_rotated_stack, seg_rotated_stack


def extract_largest_binary_object(
    bin_img: Union[xr.DataArray, np.ndarray]
) -> Union[xr.DataArray, np.ndarray]:
    """
    Extracts the largest binary object from the given binary image

    Parameters
    ----------
    bin_img
        The binary image to process

    Returns
    -------
    bin_img
        The binary image containing only the largest binary object from the input

    """
    labels = measure.label(bin_img)
    if labels.max() == 0:
        # If there are no objects in the image... simply return the image
        return bin_img
    return labels == np.argmax(np.bincount(labels.flat)[1:]) + 1


def get_area_of_largest_object(S):
    try:
        return measure.regionprops(measure.label(S))[0].area
    except IndexError:
        return 0


# def segment_pharynx(fl_img: xr.DataArray, min_area=1000, t_step=100):
#     """
#     Segment the pharynx.

#     Parameters
#     ----------
#     fl_img : xr.DataArray
#         the fluorescent image (must contain only one pharynx)
#     min_area : int, optional
#         the minimum area of the pharynx (in px), by default 500
#     t_step : int, optional
#         the amount by which the intensity may change in estimating the threshold,
#         by default 100
#     """
#     I = exposure.rescale_intensity(img_as_float(fl_img))
#     t = filters.threshold_otsu(I)

#     return I > t


def segment_pharynx(fl_img: xr.DataArray):
    seg = fl_img.copy()

    if "tl" in seg.wavelength.values[()].lower():
        seg[:] = 0
        return seg

    target_area = 450  # experimentally derived
    area_range = 100
    min_area = target_area - area_range
    max_area = target_area + area_range

    max_iter = 300

    p = 0.15
    t = fl_img.max() * p
    S = fl_img > t

    area = get_area_of_largest_object(S)

    i = 0
    while (min_area > area) or (area > max_area):
        if i >= max_iter:
            return S
        area = get_area_of_largest_object(S)

        logging.debug(f"Setting p={p}")
        if area > max_area:
            p = p + 0.01
        if area < min_area:
            p = p - 0.01
        i = i + 1

        t = fl_img.max() * p
        S = fl_img > t

        if p < 0:
            # break out if loop gets stuck w/ sensible default
            logging.warn("Caught infinite loop")
            return fl_img > (fl_img.max() * 0.15)
        if p > 0.9:
            logging.warn("Caught infinite loop")
            return fl_img > (fl_img.max() * 0.15)

    return S


def segment_pharynxes_ufunc(fl_stack) -> xr.DataArray:
    # seg = xr.apply_ufunc(
    #     segment_pharynx,
    #     fl_stack,
    #     input_core_dims=[["x", "y"]],
    #     output_core_dims=[["x", "y"]],
    #     vectorize=True,
    # )
    # return seg
    # TODO: somehow need to skip TL coordinate
    raise NotImplementedError


# noinspection PyUnresolvedReferences
def segment_pharynxes(fl_stack: xr.DataArray, threshold: int = 2000) -> xr.DataArray:
    """
    Segment the pharynxes in the given fluorescence image stack

    Parameters
    ----------
    fl_stack
        the images to segment
    threshold
        pixels with brightness above this intensity are considered to be in the pharynx

    Returns
    -------
    seg
        the image stack containing the segmented masks of the pharynxes in the input fl_stack

    Notes
    -----
    This function currently uses a static threshold to segment, then extracts the largest binary object. More
    sophisticated segmentation strategies should be tried in the future.
    """

    target_area = 450
    area_range = 100

    seg = fl_stack.copy()
    i = 0
    # STACK_ITERATION
    for img_idx in range(fl_stack.animal.size):
        for wvl_idx in range(fl_stack.wavelength.size):
            for pair in range(fl_stack.pair.size):
                for tp in fl_stack.timepoint.values:
                    selector = dict(
                        animal=img_idx, wavelength=wvl_idx, pair=pair, timepoint=tp
                    )
                    logging.debug(
                        f"Segmenting ({i}/{fl_stack.animal.size * fl_stack.wavelength.size * fl_stack.pair.size}) {selector}"
                    )
                    if fl_stack.wavelength.values[wvl_idx].lower() == "tl":
                        logging.debug("skipping TL")
                        continue
                    I = fl_stack.isel(selector)
                    S = segment_pharynx(I)
                    seg[selector] = extract_largest_binary_object(S)
                    i = i + 1
    # seg = 255 * seg.astype(np.uint8)
    seg = seg.astype(np.uint8)
    seg.loc[dict(wavelength="TL")] = 0
    return seg


def get_centroids(fl_stack: xr.DataArray, threshold=1000, gaussian_sigma=6):
    """
    Obtain the centers-of-mass for each pharynx in the given fluorescence image stack

    Parameters
    ----------
    fl_stack
        the fluorescence image stack to measure
    threshold
        the segmentation threshold for the blurred pharynx images
    gaussian_sigma
        the degree to blur the pharynxes before segmentation

    Returns
    -------
    centroids
        the centers-of-mass of each pharynx in the given stack

    """
    image_data = fl_stack.copy()
    image_data.data = ndi.gaussian_filter(
        image_data.data, sigma=(0, 0, 0, gaussian_sigma, gaussian_sigma)
    )
    image_data.data[image_data.data < threshold] = 0
    image_data.data[image_data.data > threshold] = 1

    # TODO finish implementation


def rotate(img: Union[np.ndarray, xr.DataArray], tform, orientation, order=1):
    """
    Rotate the

    Parameters
    ----------
    img
        the image to rotate
    tform
        the translation matrix to apply
    orientation
        the angle of orientation (in radians)
    order
        the order of the interpolation

    Returns
    -------
    rotated
        the translated and rotated image

    """
    # noinspection PyTypeChecker
    return transform.rotate(
        transform.warp(img, tform, preserve_range=True, mode="wrap", order=order),
        np.degrees(np.pi / 2 - orientation),
        mode="edge",
        order=order,
    )


def calculate_midlines(
    rot_seg_stack: xr.DataArray, degree: int = 4
) -> List[Dict[str, List[Polynomial]]]:
    """
    Calculate a midline for each animal in the given stack

    Parameters
    ----------
    rot_seg_stack
        The rotated mask with which midlines should be calculated.

    degree
        The degree of the polynomial fit

    Returns
    -------
    list of dict
        A list of dictionaries with the following structure::

            [
                {
                    wavelength0: [midline_pair_0, midline_pair_1, ...],
                    wavelength1: [midline_pair_0, midline_pair_1, ...]
                },
                ...
            ]
        
        accessed like so::

            midlines[img_idx][wvl][pair]

    See Also
    --------
    calculate_midline
    """
    return xr.apply_ufunc(
        calculate_midline,
        rot_seg_stack,
        input_core_dims=[["y", "x"]],
        vectorize=True,
        keep_attrs=True,
    )


def calculate_midline(
    rot_seg_img: Union[np.ndarray, xr.DataArray], degree: int = 4, pad: int = 10
) -> Polynomial:
    """
    Calculate a the midline for a single image by fitting a polynomial to the segmented
    pharynx

    Parameters
    ----------
    rot_seg_img: Union[np.ndarray, xr.DataArray]
        The rotated masked pharynx image
    degree
        the degree of the polynomial
    pad
        the number of pixels to "pad" the domain of the midline with respect to the
        boundaries of the segmentation mask

    Returns
    -------
    Polynomial
        the estimated midline

    Notes
    -----
    Right now this only works for images that this have been centered and aligned with their
    anterior-posterior along the horizontal.
    """
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            rp = measure.regionprops(measure.label(rot_seg_img))[0]
            xs, ys = rp.coords[:, 1], rp.coords[:, 0]
            left_bound, _, _, right_bound = rp.bbox
            # noinspection PyTypeChecker
            return Polynomial.fit(
                xs, ys, degree, domain=[left_bound - pad, right_bound + pad]
            )
        except IndexError:
            # Indicates trying to measure on TL for example
            return None


def measure_under_midline(
    fl: xr.DataArray,
    mid: Polynomial,
    n_points: int = 100,
    thickness: float = 0.0,
    order=1,
    norm_scale=1,
    flatten=True,
) -> np.ndarray:
    """
    Measure the intensity profile of the given image under the given midline at the given x-coordinates.

    Parameters
    ----------
    fl
        The fluorescence image to measure
    mid
        The midline under which to measure
    n_points
        The number of points to measure under
    thickness
        The thickness of the line to measure under. 
    
    Notes
    -----
    Using thickness is slower, depending on the amount of thickness 

    On my machine (2GHz Intel Core i5), as of 12/4/19:
        0-thickness:
            492 µs ± 16.6 µs per loop (mean ± std. dev. of 7 runs, 1000 loops each)
        2-thickness:
            1.99 ms ± 65.8 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)
        10-thickness:
            3.89 ms ± 92.1 µs per loop (mean ± std. dev. of 7 runs, 100 loops each)

    Returns
    -------
    zs: np.ndarray
        The intensity profile of the image measured under the midline at the given 
        x-coordinates.

    """
    # Make sure the image orientation matches with the expected order of map_coordinates
    try:
        if thickness == 0:
            xs, ys = mid.linspace(n=n_points)
            fl = np.asarray(fl)
            return ndi.map_coordinates(fl, np.stack([xs, ys]), order=1)
        else:
            # Gets a bit wonky, but makes sense

            # We need to get the normal lines from each point in the midline
            # then measure under those lines.

            # First, get the coordinates of the midline
            xs, ys = mid.linspace(n=n_points)

            # Now, we get the angles of each normal vector
            der = mid.deriv()
            normal_slopes = -1 / der(xs)
            normal_thetas = np.arctan(normal_slopes)

            # We get the x and y components of the start/end of the normal vectors
            mag = thickness / 2
            x0 = np.cos(normal_thetas) * mag
            y0 = np.sin(normal_thetas) * mag

            x1 = np.cos(normal_thetas) * -mag
            y1 = np.sin(normal_thetas) * -mag

            # These are the actual coordinates of the starts/ends of the normal vectors as they move
            # from (x,y) coordinates in the midline
            xs0 = xs + x0
            xs1 = xs + x1
            ys0 = ys + y0
            ys1 = ys + y1

            # We need to measure in a consistent direction along the normal line
            # if y0 < y1, we're going to be measuring in an opposite direction along the line... so we need flip the coordinates
            for y0, y1, x0, x1, i in zip(ys0, ys1, xs0, xs1, range(len(xs0))):
                if y0 < y1:
                    tx = xs0[i]
                    xs0[i] = xs1[i]
                    xs1[i] = tx

                    ty = ys0[i]
                    ys0[i] = ys1[i]
                    ys1[i] = ty

            n_line_pts = thickness

            all_xs = np.linspace(xs0, xs1, n_line_pts)
            all_ys = np.linspace(ys0, ys1, n_line_pts)

            straightened = ndi.map_coordinates(fl, [all_xs, all_ys])

            if flatten:
                # Create a normal distribution centered around 0 with the given scale (see scipy.norm.pdf)
                # the distribution is then tiled to be the same shape as the straightened pharynx
                # then, this resultant matrix is the weights for averaging
                w = np.tile(
                    norm.pdf(np.linspace(-1, 1, n_line_pts), scale=norm_scale),
                    (n_points, 1),
                ).T
                profile = np.average(straightened, axis=0, weights=w)

                return profile
            else:
                return straightened
    except AttributeError:
        # This happens if the image is TL. Then it will have `None` instead of
        # a midline object
        pass
    except Exception as e:
        # Here, something actually went wrong
        logging.warn(f"measuring under midline failed with error {e}")

    return np.zeros((1, n_points))


def measure_under_midlines(
    fl_stack: xr.DataArray,
    midlines: xr.DataArray,
    n_points: int = 300,
    frame_specific: bool = False,
    order=1,
    thickness=0,
    flatten=True,
) -> xr.DataArray:
    """
    Measure under all midlines in stack

    Parameters
    ----------
    fl_stack
        The fluorescence stack under which to measure
    midlines: dict
        A DataArray containing the midlines 
    n_points: int
        the number of points to sample under the midline
    frame_specific: bool
        whether to use a different midline for each frame. if False, a single midline
        will be used within all wavelengths in a pair
    thickness: float
        the thickness of the midline to measure under

    Returns
    -------
    profile_data: xr.DataArray
        the intensity profiles for each image in the stack
    """
    if not frame_specific:
        midlines.loc[dict(wavelength="470")] = midlines.sel(wavelength="410")

    measurements = xr.apply_ufunc(
        measure_under_midline,
        fl_stack,
        midlines,
        input_core_dims=[["x", "y"], []],
        output_core_dims=[["position"]],
        vectorize=True,
        keep_attrs=True,
        kwargs={"n_points": n_points, "thickness": thickness, "order": order},
    )

    measurements = measurements.assign_coords(
        {"position": np.linspace(0, 1, measurements.position.size)},
    )
    try:
        measurements = measurements.assign_coords(time=fl_stack.time)
    except AttributeError:
        pass

    return measurements


def center_of_mass_midline(rot_fl: xr.DataArray, s: float, ext: str):
    """
    Calculate the midline using the Center of Mass method

    Parameters
    ----------
    ext
    s
    rot_fl

    Returns
    -------

    """
    ys = np.arange(rot_fl.shape[0])
    midline_ys = []
    xs = np.arange(rot_fl.shape[1])
    for i in xs:
        midline_ys.append(np.average(ys, weights=rot_fl[:, i].data))
    return UnivariateSpline(xs, np.array(midline_ys), s=s, ext=ext)


def shift(image: np.ndarray, vector: np.ndarray) -> np.ndarray:
    """
    Translate the image according to the given movement vector

    Parameters
    ----------
    image
        the image to translate
    vector :
        translation parameters ``(dx, dy)``

    Returns
    -------
    img: np.ndarray
        the translated image

    """
    tform = AffineTransform(translation=vector)
    shifted = warp(image, tform, mode="wrap", preserve_range=True)
    shifted = shifted.astype(image.dtype)
    return shifted


def normalize_images_by_wvl_pair(
    fl_imgs: xr.DataArray, profiles: xr.DataArray, percent_to_clip: float = 2.0
):
    """
    Normalize images by subtracting mean profile then min-max rescaling to [0, 1]
    Parameters
    ----------
    fl_imgs
        the images to normalize
    profiles
        the intensity profiles corresponding to the images
    percent_to_clip
        how much to clip the profile when calculating mean/min/max, expressed as a percentage of the length of the profile

    Returns
    -------
    xr.DataArray
        the normalized images
    """
    idx_to_clip = int(profiles.shape[-1] * percent_to_clip / 100)
    profiles = profiles[:, idx_to_clip:-idx_to_clip]

    norm_fl = fl_imgs.copy().astype(np.float)
    for pair in fl_imgs.pair:
        for wvl in fl_imgs.wavelength.values:
            if wvl not in profiles.wavelength.values:
                continue
            for animal in range(fl_imgs.animal.size):
                prof = profiles.sel(wavelength=wvl, pair=pair).isel(animal=animal)
                img = fl_imgs.sel(wavelength=wvl, pair=pair)[animal].astype(np.float)

                # First, center according to mean
                img = img - prof.mean()

                # Then rescale to [0, 1]
                img = (img - prof.min()) / (prof.max() - prof.min())

                norm_fl.loc[dict(wavelength=wvl, pair=pair)][animal] = img

    return norm_fl


def normalize_images_single_wvl(
    fl_imgs: Union[np.ndarray, xr.DataArray],
    profiles: Union[np.ndarray, xr.DataArray],
    percent_to_clip: float = 2.0,
) -> Union[np.ndarray, xr.DataArray]:
    """
    Normalize single wavelength image stack by subtracting the mean of the corresponding
    intensity profile, then min-max rescaling to [0, 1]

    Parameters
    ----------
    fl_imgs
        an array-like structure of shape (frame, row, col)
    profiles
        an array-like structure of shape (frame, position_along_midline)
    percent_to_clip
        how much to clip the profile when calculating mean/min/max, expressed as a percentage of the length of the profile

    Returns
    -------
    Union[np.ndarray, xr.DataArray]
        normalized images
    """

    if fl_imgs.ndim != 3:
        raise ValueError("images must have shape (frame, row, col)")
    if profiles.ndim != 2:
        raise ValueError("profiles must have shape (frame, position_along_midline)")

    normed_imgs = fl_imgs.copy().astype(np.float32)

    idx_to_clip = int(profiles.shape[-1] * percent_to_clip / 100)
    profiles = profiles[:, idx_to_clip:-idx_to_clip]

    prof_means = np.mean(profiles, axis=1)

    profiles = profiles - prof_means
    normed_imgs = normed_imgs - prof_means

    prof_mins = np.min(profiles, axis=1)
    prof_maxs = np.max(profiles, axis=1)

    normed_imgs = (normed_imgs - prof_mins) / (prof_maxs - prof_mins)

    return normed_imgs


def z_normalize_with_masks(imgs, masks):
    """
    Perform z-normalization [0] on the entire image (relative to the content within the masks).

    That is to say, we center the pixels (within the mask) such that their mean is 0, and ensure their standard deviation is ~1.

    This allows us to see spatial patterns within the masked region (even if pixels outside of the masked region
    fall very far above or below those inside) by setting the colormap center around 0.

    [0] - https://jmotif.github.io/sax-vsm_site/morea/algorithm/znorm.html
    """
    masked = ma.masked_array(imgs, np.logical_not(masks))
    mu = np.mean(masked, axis=(-2, -1), keepdims=True)
    sigma = np.std(masked, axis=(-2, -1), keepdims=True)

    return (imgs - mu) / sigma


def create_normed_rgb_ratio_stack(
    r_imgs, seg_imgs, vmin=-7, vmax=7, cmap="coolwarm", output_filename=None
):
    """
    Z-normalize the images (relative to the masks), then transform them into RGB with the given colormap
    """
    r_znormed = z_normalize_with_masks(r_imgs, seg_imgs)
    normalizer = mpl.colors.Normalize(vmin=vmin, vmax=vmax)
    if isinstance(cmap, str):
        cmap = plt.get_cmap(cmap)
    # TODO generalize dtype? for now, 32-bit only
    rgb_img = cmap(normalizer(r_znormed))[:, :, :, :3].astype(np.float16)

    if output_filename is not None:
        with open(output_filename, "wb") as f:
            io.imsave(f, rgb_img)

    return rgb_img


def get_bbox(m, pad=5):
    try:
        y_min, x_min, y_max, x_max = np.array(regionprops(label(m))[0].bbox)

        y_min = max(int(y_min - (pad / 2)), 0)
        x_min = max(int(x_min - (pad / 2)), 0)
        y_max = min(int(y_max + (pad / 2)), m.shape[0])
        x_max = min(int(x_max + (pad / 2)), m.shape[1])

        return np.array([y_min, x_min, y_max, x_max]).astype(np.float)
    except IndexError:
        return [np.nan, np.nan, np.nan, np.nan]


def bspline_intra_modal_registration(
    fixed_image,
    moving_image,
    fixed_image_mask=None,
    fixed_points=None,
    moving_points=None,
    ylim=None,
    point_width=5.0,
):

    registration_method = sitk.ImageRegistrationMethod()

    # Determine the number of BSpline control points using the physical spacing we want for the control grid.
    grid_physical_spacing = [
        point_width,
        point_width,
        point_width,
    ]  # A control point every 50mm
    image_physical_size = [
        size * spacing
        for size, spacing in zip(fixed_image.GetSize(), fixed_image.GetSpacing())
    ]
    mesh_size = [
        int(image_size / grid_spacing + 0.5)
        for image_size, grid_spacing in zip(image_physical_size, grid_physical_spacing)
    ]

    initial_transform = sitk.BSplineTransformInitializer(
        image1=fixed_image, transformDomainMeshSize=mesh_size, order=2
    )
    registration_method.SetInitialTransform(initial_transform)

    registration_method.SetMetricAsMeanSquares()
    # Settings for metric sampling, usage of a mask is optional. When given a mask the sample points will be
    # generated inside that region. Also, this implicitly speeds things up as the mask is smaller than the
    # whole image.
    # registration_method.SetMetricSamplingStrategy(registration_method.RANDOM)
    # registration_method.SetMetricSamplingPercentage(0.1)
    if fixed_image_mask:
        registration_method.SetMetricFixedMask(fixed_image_mask)

    # Multi-resolution framework.
    # registration_method.SetShrinkFactorsPerLevel(shrinkFactors = [4,2,1])
    # registration_method.SetShrinkFactorsPerLevel(shrinkFactors = [2,1])
    # registration_method.SetSmoothingSigmasPerLevel(smoothingSigmas=[1,0])
    # registration_method.SmoothingSigmasAreSpecifiedInPhysicalUnitsOn()

    registration_method.SetInterpolator(sitk.sitkLinear)
    registration_method.SetOptimizerAsLBFGSB(
        gradientConvergenceTolerance=1e-5, numberOfIterations=10
    )
    # registration_method.SetOptimizerAsGradientDescent(learningRate=1.0, numberOfIterations=50, convergenceMinimumValue=1e-6, convergenceWindowSize=10)
    # registration_method.SetOptimizerAsAmoeba()
    # registration_method.SetOptimizerAsAmoeba()
    # registration_method.SetOptimizerAsLBFGS2(numberOfIterations=1000)

    # registration_method.AddCommand(sitk.sitkStartEvent, start_plot)
    # registration_method.AddCommand(sitk.sitkEndEvent, end_plot)
    # registration_method.AddCommand(
    #     sitk.sitkMultiResolutionIterationEvent, update_multires_iterations
    # )
    # registration_method.AddCommand(sitk.sitkIterationEvent, lambda: plot_values(registration_method, ylim=ylim))

    # If corresponding points in the fixed and moving image are given then we display the similarity metric
    # and the TRE during the registration.
    # if fixed_points and moving_points:
    #     registration_method.AddCommand(
    #         sitk.sitkStartEvent, rc.metric_and_reference_start_plot
    #     )
    #     registration_method.AddCommand(
    #         sitk.sitkEndEvent, rc.metric_and_reference_end_plot
    #     )
    #     registration_method.AddCommand(
    #         sitk.sitkIterationEvent,
    #         lambda: rc.metric_and_reference_plot_values(
    #             registration_method, fixed_points, moving_points
    #         ),
    #     )

    return registration_method.Execute(fixed_image, moving_image)


def register_image(fixed, moving, mask=None, point_width=5.0):
    z_fixed = zscore(fixed.values)
    z_moving = zscore(moving.values)

    if mask is not None:
        mask = sitk.GetImageFromArray(mask * 255)

    tx = bspline_intra_modal_registration(
        sitk.GetImageFromArray(z_fixed),
        sitk.GetImageFromArray(z_moving),
        fixed_image_mask=mask,
        point_width=point_width,
    )

    reg_moving = sitk.GetArrayFromImage(
        sitk.Resample(
            sitk.GetImageFromArray(moving),
            sitk.GetImageFromArray(fixed),
            tx,
            sitk.sitkLinear,
        )
    )

    return reg_moving


def crop(img, bbox):
    y_min, x_min, y_max, x_max = bbox.values.astype(np.int)

    return img[y_min:y_max, x_min:x_max]


def register_all_images(
    imgs,
    masks,
    bbox_pad=10,
    point_width=6.0,
    fixed_wvl="410",
    moving_wvl="470",
    mask_wvl="410",
):
    bboxes = xr.apply_ufunc(
        get_bbox,
        masks,
        input_core_dims=[["y", "x"]],
        output_core_dims=[["pos"]],
        vectorize=True,
        kwargs={"pad": bbox_pad},
    ).assign_coords({"pos": ["min_row", "max_row", "min_col", "max_col"]})

    reg_imgs = imgs.copy()

    for animal in imgs.animal:
        for pair in imgs.pair:
            for timepoint in imgs.timepoint:
                fixed = imgs.sel(
                    animal=animal, pair=pair, timepoint=timepoint, wavelength=fixed_wvl
                )
                moving = imgs.sel(
                    animal=animal, pair=pair, timepoint=timepoint, wavelength=moving_wvl
                )
                mask = masks.sel(
                    animal=animal, pair=pair, timepoint=timepoint, wavelength=mask_wvl
                )
                bbox = bboxes.sel(
                    animal=animal, pair=pair, timepoint=timepoint, wavelength=mask_wvl
                )

                # crop image
                crop_fixed = crop(fixed, bbox)
                crop_moving = crop(moving, bbox)
                crop_mask = crop(mask, bbox)

                # register image
                reg_moving = register_image(
                    crop_fixed, crop_moving, mask=crop_mask, point_width=point_width
                )

                # paste cropped images back into correct location (from bbox)
                y_min, x_min, y_max, x_max = bbox.values.astype(np.int)
                reg_imgs.loc[
                    dict(
                        animal=animal,
                        pair=pair,
                        timepoint=timepoint,
                        wavelength=moving_wvl,
                    )
                ][y_min:y_max, x_min:x_max] = reg_moving

    return reg_imgs