from slicegan import preprocessing, util
import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch.optim as optim
import time
import matplotlib


def train(pth, imtype, datatype, real_data, Disc, Gen, nc, l, nz, sf, lz, num_epochs):
    """
    train the generator
    :param pth: path to save all files, imgs and data
    :param imtype: image type e.g nphase, colour or gray
    :param datatype: training data format e.g. tif, jpg ect
    :param real_data: path to training data
    :param Disc:
    :param Gen:
    :param nc: channels
    :param l: image size
    :param nz: latent vector size
    :param sf: scale factor for training data
    :return:
    """
    if len(real_data) == 1:
        real_data *= 3
        isotropic = True
    else:
        isotropic = False

    print('Loading Dataset...')
    dataset_xyz = preprocessing.batch(real_data, datatype, l, sf)

    ## Constants for NNs
    matplotlib.use('Agg')
    ngpu = 1
    # num_epochs = 30

    # batch sizes
    batch_size = 32
    D_batch_size = 8
    # optimiser params for G and D
    
    lrg = 0.0001
    lrd = 0.0001
    #change values of beta1 between 0.1-0.9, beta2 0.9-0.99 and calc evals?
    beta1 = 0
    beta2 = 0.9
    Lambda = 10
    critic_iters = 5
    cudnn.benchmark = True
    workers = 0
    # lz = 4
    # The value of lz is now calculated and passed as a computed parameter

    ##Dataloaders for each orientation
    device = torch.device("cuda:0" if (torch.cuda.is_available() and ngpu > 0) else "cpu")
    print(device, " will be used.\n")

    # D trained using different data for x, y and z directions
    dataloaderx = torch.utils.data.DataLoader(dataset_xyz[0], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloadery = torch.utils.data.DataLoader(dataset_xyz[1], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloaderz = torch.utils.data.DataLoader(dataset_xyz[2], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)

    # Create the Genetator network
    netG = Gen().to(device)
    if ('cuda' in str(device)) and (ngpu > 1):
        netG = nn.DataParallel(netG, list(range(ngpu)))
    optG = optim.Adam(netG.parameters(), lr=lrg, betas=(beta1, beta2))

    # Define 1 Discriminator and optimizer for each plane in each dimension
    netDs = []
    optDs = []
    for i in range(3):
        netD = Disc()
        netD = (nn.DataParallel(netD, list(range(ngpu)))).to(device)
        netDs.append(netD)
        optDs.append(optim.Adam(netDs[i].parameters(), lr=lrd, betas=(beta1, beta2)))

    disc_real_log = []
    disc_fake_log = []
    gp_log = []
    Wass_log = []

    # gk, gs, gp = netG.params[4,5,7]

    print("Starting Training Loop...")
    # For each epoch
    start = time.time()
    for epoch in range(num_epochs):
        # sample data for each direction
        for i, (datax, datay, dataz) in enumerate(zip(dataloaderx, dataloadery, dataloaderz), 1):
            dataset = [datax, datay, dataz]
            ### Initialise
            ### Discriminator
            ## Generate fake image batch with G
            noise = torch.randn(D_batch_size, nz, lz, lz, lz, device=device)
            fake_data = netG(noise).detach()
            
            # for each dim (d1, d2 and d3 are used as permutations to make 3D volume into a batch of 2D images)
            for dim, (netD, optimizer, data, d1, d2, d3) in enumerate(
                    zip(netDs, optDs, dataset, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                if isotropic:
                    netD = netDs[0]
                    optimizer = optDs[0]
                netD.zero_grad()
                ##train on real images
                real_data = data[0].to(device)
                out_real = netD(real_data).view(-1).mean()
                ## train on fake images
                # perform permutation + reshape to turn volume into batch of 2D images to pass to D
                # Round n
                    # 0 2 1 3 4
                    # 0 3 1 2 4
                    # 0 4 1 2 3
                fake_data_perm = fake_data.permute(0, d1, 1, d2, d3).reshape(l * D_batch_size, nc, l, l)
                out_fake = netD(fake_data_perm).mean()
                gradient_penalty = util.calc_gradient_penalty(netD, real_data, fake_data_perm[:batch_size],
                                                              batch_size, l,
                                                              device, Lambda, nc)
                disc_cost = out_fake - out_real + gradient_penalty
                disc_cost.backward()
                optimizer.step()
            # logs for plotting
            disc_real_log.append(out_real.item())
            disc_fake_log.append(out_fake.item())
            Wass_log.append(out_real.item() - out_fake.item())
            gp_log.append(gradient_penalty.item())
            ### Generator Training
            if i % int(critic_iters) == 0:
                netG.zero_grad()
                errG = 0
                noise = torch.randn(batch_size, nz, lz, lz, lz, device=device)
                fake = netG(noise)

                for dim, (netD, d1, d2, d3) in enumerate(
                        zip(netDs, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                    if isotropic:
                        # only need one D
                        netD = netDs[0]
                    # permute and reshape to feed to disc
                    fake_data_perm = fake.permute(0, d1, 1, d2, d3).reshape(l * batch_size, nc, l, l)
                    output = netD(fake_data_perm)
                    errG -= output.mean()
                    # Calculate gradients for G
                errG.backward()
                optG.step()

            # Output training stats & show imgs
            if i % 25 == 0:
                # Put model into evaluation mode
                netG.eval()
                with torch.no_grad():
                    torch.save(netG.state_dict(), pth + '_Gen.pt')
                    torch.save(netD.state_dict(), pth + '_Disc.pt')
                    noise = torch.randn(1, nz, lz, lz, lz, device=device)
                    img = netG(noise)
                    ###Print progress
                    ## calc ETA
                    steps = len(dataloaderx)
                    util.calc_eta(steps, time.time(), start, i, epoch, num_epochs)
                    ###save example slices
                    util.test_plotter(img, 5, imtype, pth)
                    # plotting graphs
                    util.graph_plot([disc_real_log, disc_fake_log], ['real', 'perp'], pth, 'LossGraph')
                    util.graph_plot([Wass_log], ['Wass Distance'], pth, 'WassGraph')
                    util.graph_plot([gp_log], ['Gradient Penalty'], pth, 'GpGraph')
                
                # Put model into training mode
                netG.train()


def check_conv_vals(k, s, p):
    if s < k and k % s == 0 and p >= k - s:
        return 1
    else:
        return 0


def calc_lz(im_size, gk, gs, gp):
    # im_size-> original image size
    # gk, gs, gp-> generator kernel size, stride, and padding

    gk, gs, gp = gk[::-1], gs[::-1], gp[::-1] # because, we want to go in reverse to calculate lz from image size

    for lay, (k, s, p) in enumerate(zip(gk, gs, gp)):

        ch = check_conv_vals(k, s, p)
        if ch == 0:
            print("Values not compatible for uniform information density in the generator samples.")
            break

        if lay == 0:
            next_l = im_size

        # \/ equation to calculate size in next layer given transpose convolution (reverse this to find lz)
        # next_l = k + ((l - 1) * s) - 2 * p
        # next_l - k + 2 * p = (l - 1) * s
        # l = ((next_l - k + 2 * p) / s) + 1

        next_l = ((next_l - k + 2 * p)/s) + 1
        next_l = int(next_l)

    return next_l
