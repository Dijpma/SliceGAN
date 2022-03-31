from run_slicegan import dk, ds, dp, df
from slicegan import preprocessing, util
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch.optim as optim
import time
import matplotlib
import cv2
import torch.nn.functional as F
import pickle
import math
from cv2 import SimpleBlobDetector


class CircleNet(nn.Module):
    def __init__(self, dk, ds, dp, df):
        super(CircleNet, self).__init__()
        self.convs = nn.ModuleList()
        for lay, (k, s, p) in enumerate(zip(dk, ds, dp)):
            self.convs.append(nn.Conv2d(df[lay], df[lay + 1], k, s, p, bias=False))

    def forward(self, x):
        for conv in self.convs[:-1]:
            x = F.relu_(conv(x))
        x = self.convs[-1](x)
        return x


def trainCNet(datatype, realData, l, sf, CNet):
    """
        train the network to detect and count circles
        :param datatype: training data format e.g. tif, jpg ect
        :param real_data: path to training data
        :param nc: channels
        :param CNet:
        :param l: image size
        :param nz: latent vector size
        :param sf: scale factor for training data
        :return:
    """

    if len(realData) == 1:
        realData *= 3

    print('Loading Dataset...')
    dataset_xyz = preprocessing.batch(realData, datatype, l, sf)

    ## Constants for NNs
    # matplotlib.use('Agg')
    ngpu = 1
    numEpochs = 30

    # batch sizes
    batch_size = 8
    # optimiser params
    lrc = 0.0001
    Beta1 = 0.9  # Different value as the use case here is fairly standard and therefore would benefit from a non-zero initialization of Beta1
    Beta2 = 0.99
    circle_dim = 0
    cudnn.benchmark = True
    workers = 0

    device = torch.device("cuda:0" if (torch.cuda.is_available() and ngpu > 0) else "cpu")
    print(device, " will be used.\n")

    # Data Loaded along the dimension where circles are to be observed and counted
    dataLoader = torch.utils.data.DataLoader(dataset_xyz[circle_dim], batch_size=batch_size, shuffle=True,
                                             num_workers=workers)

    # Define Network

    cNet = CNet().to(device)
    if ('cuda' in str(device)) and (ngpu > 1):
        cNet = nn.DataParallel(cNet, list(range(ngpu)))
    optC = optim.Adam(cNet.parameters(), lr=lrc, betas=(Beta1, Beta2))
    cNet.zero_grad()

    print("Starting CNet Training...")

    realData = dataLoader.to(device)

    for e in range(numEpochs):

        minAr, maxAr = 100000, 0

        iterc = 0
        LList = []

        for R in realData:
            pred_OutR = cNet(R).view(-1)
            if e == 0:
                real_OutR, min_area, max_area = numCircles(R, 1)

                if min_area < minAr:
                    minAr = min_area
                if max_area > maxAr:
                    maxAr = max_area
            else:
                real_OutR = numCircles(R, 2, minAr, maxAr)

            predR, realR = int(pred_OutR), int(real_OutR)

            iterc += 1

            print(f"Epoch {e} : Slice {iterc} - NRC {realR} NPR {predR} Diff {predR - realR}\n")

            cLoss = (pred_OutR - real_OutR)**2 if pred_OutR > real_OutR else 0
            LList.append(cLoss)

        if e == 0:
            print(f"\n\n Circle Area Thresholds: minArea = {minAr} & maxArea = {maxAr} \n\n")

        lsum = 0
        for ll in LList:
            lsum += ll

        CLoss = lsum/iterc

        CLoss.backward()
        optC.step()


def CircleWeights(cnet, WeightPath, SL=bool(True)):
    """
    :param cnet: circlenet model
    :param WeightPath: Path to save or load weights
    :param SL: flag parameter to determine whether weights need to be saved or loaded
    :return:
    """

    if SL:
        torch.save(cnet.state_dict(), WeightPath)
    else:
        cnet.load_state_dict(torch.load(WeightPath))
        return cnet


def numCircles(slice_i, area_find = 3, MinArea = 0, MaxArea = 100):
    """
    :param slice_i: slice in which number of circles is to be calculated
    :param area_find: 1-> find and return min and max area; 2->filter by area; 3-> no filter, no find;
    :param MinArea: only used for calling in area_find=2
    :param MaxArea: only used for calling in area_find=2
    :return:
    """
    params = cv2.SimpleBlobDetector_Params()
    sizepoints = []

    params.filterByCircularity = True
    params.minCircularity = 0.9

    if area_find == 1:

        ## We want to find max and min area of circles in the slice to be used later

        detector = cv2.SimpleBlobDetector_create(params)
        keypoints = detector.detect(slice_i)
        for k in keypoints:
            sizepoints.append(k.size())

        kmax = ((max(sizepoints)/2)**2)*math.pi
        kmin = ((min(sizepoints)/2)**2)*math.pi
        return len(keypoints), kmin, kmax

    elif area_find == 2:

        params.filterByArea = True
        params.minArea = MinArea
        params.maxArea = MaxArea

        detector = cv2.SimpleBlobDetector_create(params)
        keypoints = detector.detect(slice_i)

        return len(keypoints)

    else:
        detector = cv2.SimpleBlobDetector_create(params)
        keypoints = detector.detect(slice_i)

        return len(keypoints)


def CircularityLoss(imreal, imfake, CL_CNET):
    realcirc, fakecirc, diffcircL = []
    rlen, flen = 0
    D = 0

    for r in imreal:
        realcirc.append(CL_CNET(r))
        rlen += 1

    for f in imfake:
        fakecirc.append(CL_CNET(f))
        gg = numCircles(f)
        print(f"Slice {f} has a difference of {CL_CNET(f) - gg} \n")
        flen += 1

    if rlen != flen:
        print("\n The number of real and fake slices do not match")
        return 0

    for i, R, F in enumerate(zip(realcirc, fakecirc)):
        diffcirc = ((F - R) ** 2) if R > F else 0  # 0 can also be substituted by int((R-F)**2)
        diffcircL.append(diffcirc)

        print(f"Slice {i} has a difference of {diffcirc} circles between real and fake \n")

    for diff in diffcircL:
        D += diff

    return D/rlen
