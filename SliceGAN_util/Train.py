import torch
import torch.nn as nn
import torch.backends.cudnn as cudnn
import torch.optim as optim
import numpy as np
import matplotlib.pyplot as plt
import time
import matplotlib
from SliceGAN_util import BatchMaker, Train_tools
import tifffile
def trainer(pth, imtype, datatype, real_data, Disc, Gen, isotropic, nc, l, nz, sf):
    if len(real_data) == 1:
        real_data *= 3
    print('Loading Dataset...')
    datasetxyz = BatchMaker.Batch(real_data[0], real_data[1], real_data[2], datatype, l, sf,TI=True)
    ## Constants for NNs
    matplotlib.use('Agg')
    ngpu = 1
    batch_size = 32
    D_batch_size = 8
    num_epochs = 30
    lrg = 0.0004
    lr = 0.0001
    beta1 = 0
    beta2 = 0.9
    Lambda = 10
    critic_iters = 5
    cudnn.benchmark = True
    workers = 0

    ##Dataloaders for each orientation
    device = torch.device("cuda:0" if(torch.cuda.is_available() and ngpu > 0) else "cpu")
    print(device, " will be used.\n")


    dataloaderx = torch.utils.data.DataLoader(datasetxyz[0], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloadery = torch.utils.data.DataLoader(datasetxyz[1], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloaderz = torch.utils.data.DataLoader(datasetxyz[2], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)

    # Create the Genetator network
    netG = Gen().to(device)
    if ('cuda' in str(device)) and (ngpu > 1):
        netG = nn.DataParallel(netG, list(range(ngpu)))
    optG = optim.Adam(netG.parameters(), lr=lrg, betas=(beta1, beta2))

    # Define 1 discriminator and optimizer for each plane in each dimension
    netDs = []
    optDs = []
    for i in range(3):
        netD = Disc()
        netD = (nn.DataParallel(netD, list(range(ngpu)))).to(device)
        netDs.append(netD)
        optDs.append(optim.Adam(netDs[i].parameters(), lr=lr, betas=(beta1, beta2)))

    disc_real_log = []
    disc_fake_log = []
    gp_log = []
    Wass_log = []

    print("Starting Training Loop...")
    # For each epoch
    start = time.time()
    for epoch in range(num_epochs):
        # For each batch in the dataloader
        for i, (datax, datay, dataz) in enumerate(zip(dataloaderx, dataloadery, dataloaderz), 1):
            dataset = [datax, datay, dataz]
            ### Initialise
            ### Discriminator
            ## Generate fake image batch with G
            noise = torch.randn(D_batch_size, nz, 4, 4, 4, device=device)
            fake_data = netG(noise).detach()
            # For each dimension
            start_disc = time.time()
            for dim, (netD, optimizer, data, d1, d2, d3) in enumerate(
                    zip(netDs, optDs, dataset, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                if isotropic:
                    netD = netDs[0]
                    optimizer = optDs[0]
                for p in netD.parameters():  # reset requires_grad
                    p.requires_grad_(True)
                ##train on real images
                real_data = data[0].to(device)
                netD.zero_grad()
                # Forward pass real batch through D
                out_real = netD(real_data).view(-1).mean()
                # train on fake images
                gradient_penalty = 0
                fake_data_perm = fake_data.permute(0, d1, 1, d2, d3).reshape(l * D_batch_size, nc, l, l)
                out_fake = netD(fake_data_perm).mean()
                gradient_penalty += Train_tools.calc_gradient_penalty(netD, real_data, fake_data_perm[:batch_size],
                                                                      batch_size, l,
                                                                      device, Lambda, nc)
                backg_start = time.time()
                disc_cost = out_fake - out_real + gradient_penalty
                disc_cost.backward()
                optimizer.step()

            # print('Dback 1 run: ', time.time() - backg_start)
            # fin_disc = time.time() - start_disc
            # print('1 disc run 3dir: ', fin_disc)
            disc_real_log.append(out_real.item())
            disc_fake_log.append(out_fake.item())
            Wass_log.append(out_real.item() - out_fake.item())
            gp_log.append(gradient_penalty.item())
            ### Generator Training
            start_gen = time.time()
            if i % int(critic_iters) == 0:
                netG.zero_grad()
                errG = 0
                noise = torch.randn(batch_size, nz, 4, 4, 4, device=device)
                fake = netG(noise)
                for dim, (netD, d1, d2, d3) in enumerate(
                        zip(netDs, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                    if isotropic:
                        netD = netDs[0]
                    for p in netD.parameters():
                        p.requires_grad_(False)
                    # For each plane
                    fake_data_perm = fake.permute(0, d1, 1, d2, d3).reshape(l * batch_size, nc, l, l)
                    output = netD(fake_data_perm)
                    errG -= output.mean()
                    # Calculate gradients for G
                Gback_start = time.time()
                errG.backward()
                # print('Gback: ', time.time() - Gback_start)
                optG.step()
                # Output training stats & show imgs
                fin_gen = time.time() - start_gen
                # print('gen 1 run: ', fin_gen)
            if i % 25 == 0:
                start_save = time.time()
                torch.save(netG.state_dict(), pth + '_Gen.pt')
                torch.save(netD.state_dict(), pth + '_Disc.pt')
                noise = torch.randn(1, nz, 4, 4, 4, device=device)
                img = netG(noise)
                ###Print progress
                ## calc ETA
                steps = len(dataloaderx)
                Train_tools.calc_ETA(steps, time.time(), start, i, epoch, num_epochs)
                ###save example slices
                Train_tools.TestPlotter(img, 5, imtype, pth)
                # plotting graphs
                Train_tools.GraphPlot([disc_real_log, disc_fake_log], ['real', 'perp'], pth, 'LossGraph')
                Train_tools.GraphPlot([Wass_log], ['Wass Distance'], pth, 'WassGraph')
                Train_tools.GraphPlot([gp_log], ['Gradient Penalty'], pth, 'GpGraph')
                fin_save = time.time() - start_save
                # print('save: ', fin_save)
            if i % 300 == 0:
                noise = torch.randn(1, nz, 8, 8, 8, device=device)
                raw = netG(noise)
                gb = Train_tools.PostProc(raw, imtype)
                tif = np.int_(gb)
                tifffile.imwrite(pth + '_epoch_' + str(epoch) + '_iter_' + str(i) + '.tif', tif)

def transfer_trainer(pth,imtype,datatype,real_data, Disc, Gen, isotropic, nc, l, nz, trans_paths, al, sf):
    if len(real_data) == 1:
        real_data*=3
    print('Loading Dataset...')
    datasetxyz = BatchMaker.Batch(real_data[0], real_data[1], real_data[2], datatype, l, sf, TI = False)
    ## Constants for NNs
    matplotlib.use('Agg')
    ngpu=1
    batch_size=32
    D_batch_size = 8
    num_epochs=3
    lrg=0.0004
    lr =0.0001
    beta1 =0
    beta2 =0.9
    Lambda =10
    critic_iters = 5
    # cudnn.benchmark = True
    workers = 0

    ##Dataloaders for each orientation
    device = torch.device("cuda:0" if(torch.cuda.is_available() and ngpu > 0) else "cpu")
    print(device, " will be used.\n")
    # device = torch.device('cpu')

    dataloaderx = torch.utils.data.DataLoader(datasetxyz[0], batch_size=batch_size,
                                             shuffle=True, num_workers=workers)
    dataloadery = torch.utils.data.DataLoader(datasetxyz[1], batch_size=batch_size,
                                             shuffle=True, num_workers=workers)
    dataloaderz = torch.utils.data.DataLoader(datasetxyz[2], batch_size=batch_size,
                                             shuffle=True, num_workers=workers)

    # Create the Genetator network
    netG = Gen().to(device)
    netG.load_state_dict(torch.load(trans_paths[0]))
    if('cuda' in str(device)) and (ngpu > 1):
        netG = nn.DataParallel(netG, list(range(ngpu)))
    optG = optim.Adam(netG.parameters(), lr=lrg, betas=(beta1, beta2))

    m = 5 - al
    for q, (p, name) in enumerate(zip(netG.parameters(), netG.state_dict())):  # reset requires_grad
        if 5 > q > al - 1 or q > ((al - 1) * 2) + 6:
            p.requires_grad_(False)
            print('Gen lay off: ', q)
        elif 'conv' in name:
            print('Gen lay conv init rand:', q)
            # nn.init.normal_(p, 0, 0.02)
        else:
            if q % 2 != 0:
                # nn.init.normal_(p, 1.0, 0.02)
                print('Gen lay weight init rand:', q)
            else:
                # nn.init.constant_(p, 0)
                print('Gen lay bias init rand:', q)
    # Define 1 discriminator and optimizer for each plane in each dimension
    netDs = []
    optDs = []
    torch.device_ids = None
    for i in range(3):
        netD = Disc()
        netD = (nn.DataParallel(netD, list(range(ngpu)))).to(device)
        netD.load_state_dict(torch.load(trans_paths[1]))
        netD.device_ids = None
        for q, p in enumerate(netD.parameters()):  # reset requires_grad
            if q > m-1:
                # nn.init.normal_(p,0,0.02)
                print('Disc lay rand init: ', q)
            else:
                p.requires_grad_(False)
                print('disc lay off: ', q)
        netDs.append(netD)
        optDs.append(optim.Adam(netDs[i].parameters(), lr=lr, betas=(beta1, beta2)))

    disc_real_log=[]
    disc_fake_log=[]
    gp_log=[]
    Wass_log = []

    print("Starting Training Loop...")
    # For each epoch
    start = time.time()
    for epoch in range(num_epochs):
        # For each batch in the dataloader
        for i, (datax, datay, dataz) in enumerate(zip(dataloaderx, dataloadery, dataloaderz),1):
            dataset = [datax,datay,dataz]
            ### Initialise
            ### Discriminator
            ## Generate fake image batch with G
            noise = torch.randn(D_batch_size, nz, 4,4,4, device=device)
            fake_data = netG(noise).detach()
            #For each dimension
            start_disc = time.time()
            for dim, (netD, optimizer, data, d1, d2, d3) in enumerate(zip(netDs, optDs, dataset, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                if isotropic:
                    netD = netDs[0]
                    optimizer = optDs[0]
                for q,p in enumerate(netD.parameters()):  # reset requires_grad
                    if q > m - 1:
                        p.requires_grad_(True)

                ##train on real images
                real_data = data[0].to(device)
                netD.zero_grad()
                # Forward pass real batch through D
                out_real = netD(real_data).view(-1).mean()
                #train on fake images
                gradient_penalty = 0
                fake_data_perm = fake_data.permute(0, d1, 1, d2, d3).reshape(l * D_batch_size, nc, l, l)
                out_fake = netD(fake_data_perm).mean()/(l * D_batch_size/batch_size)
                gradient_penalty += Train_tools.calc_gradient_penalty(netD, real_data, fake_data_perm[:batch_size], batch_size, l,
                                                                      device, Lambda, nc)
                backg_start = time.time()
                disc_cost = out_fake - out_real + gradient_penalty
                disc_cost.backward()
                optimizer.step()

            # print('Dback 1 run: ', time.time() - backg_start)
            # fin_disc = time.time() - start_disc
            # print('1 disc run 3dir: ', fin_disc)
            disc_real_log.append(out_real.item())
            disc_fake_log.append(out_fake.item())
            Wass_log.append(out_real.item()- out_fake.item())
            gp_log.append(gradient_penalty.item())
            ### Generator Training
            start_gen = time.time()
            if i % int(critic_iters) == 0.5:
                GL_Tot = 0  # Gen Loss (ideal 0)
                netG.zero_grad()
                errG=0
                noise = torch.randn(batch_size, nz, 4,4,4, device=device)
                noise.requires_grad_(True)
                fake = netG(noise)
                # For each dimension
                #del from here

                #to here
                for dim, netD in enumerate(netDs):
                    if isotropic:
                        netD = netDs[0]
                    for p in netD.parameters():
                        p.requires_grad_(False)
                    #For each plane
                    for lyr in range(l):
                        # Pass through relevant discriminator
                        if dim==0:
                            output = netD(fake[:, :, lyr, :, :]).view(-1)
                        elif dim==1:
                            output = netD(fake[:, :, :, lyr, :]).view(-1)
                        else:
                            output = netD(fake[:, :, :, :, lyr]).view(-1)
                        #Calculate error for this plane
                        errG -= output.mean()/l
                        GL_Tot += output.mean()/(l*3)
                        # Calculate gradients for G
                Gback_start = time.time()
                errG.backward()
                # print('Gback: ', time.time() - Gback_start)
                optG.step()
            # Output training stats & show imgs
                fin_gen = time.time() - start_gen
                # print('gen 1 run: ', fin_gen)
            if i % 25 == 0:
                start_save = time.time()
                torch.save(netG.state_dict(), pth + '_Gen.pt')
                torch.save(netD.state_dict(), pth + '_Disc.pt')
                noise = torch.randn(1, nz, 4,4,4, device = device)
                img = netG(noise)
                ###Print progress
                ## calc ETA
                steps = len(dataloaderx)
                Train_tools.calc_ETA(steps, time.time(), start, i, epoch, num_epochs)
                ###save example slices
                Train_tools.TestPlotter(img, 5, imtype, pth)
                # plotting graphs
                Train_tools.GraphPlot([disc_real_log, disc_fake_log],['real', 'perp'], pth, 'LossGraph')
                Train_tools.GraphPlot([Wass_log], ['Wass Distance'], pth, 'WassGraph')
                Train_tools.GraphPlot([gp_log], ['Gradient Penalty'], pth, 'GpGraph')
                fin_save = time.time() - start_save
                # print('save: ', fin_save)
            if i% 300 ==0:
                noise = torch.randn(1, nz, 8,8,8, device = device)
                raw = netG(noise)
                gb = Train_tools.PostProc(raw, imtype)
                tif = np.int_(gb)
                tifffile.imwrite(pth + '_epoch_'+ str(epoch) + '_iter_' + str(i) + '.tif', tif)
    # print(str(al) + ' Active Layers run time:', start - time.time())

def conditional_trainer(pth, imtype, datatype, real_data, labels, Disc, Gen, isotropic, nc, l, nz, sf):
    print('Loading Dataset...')
    datasetxyz = BatchMaker.CBatch(real_data,labels, datatype, l, sf,TI=True)
    ## Constants for NNs
    # matplotlib.use('Agg')
    ngpu = 1
    nlabels = len(labels[0])
    batch_size = 16
    D_batch_size = 12
    num_epochs = 30
    lrg = 0.0002
    lr = 0.00005
    beta1 = 0
    beta2 = 0.9
    Lambda = 10
    critic_iters = 5
    cudnn.benchmark = True
    workers = 0

    ##Dataloaders for each orientation
    device = torch.device("cuda:0" if(torch.cuda.is_available() and ngpu > 0) else "cpu")
    print(device, " will be used.\n")
    # device = torch.device('cpu')

    dataloaderx = torch.utils.data.DataLoader(datasetxyz[0], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloadery = torch.utils.data.DataLoader(datasetxyz[1], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    dataloaderz = torch.utils.data.DataLoader(datasetxyz[2], batch_size=batch_size,
                                              shuffle=True, num_workers=workers)
    # Create the Genetator network
    netG = Gen().to(device)
    if ('cuda' in str(device)) and (ngpu > 1):
        netG = nn.DataParallel(netG, list(range(ngpu)))
    optG = optim.Adam(netG.parameters(), lr=lrg, betas=(beta1, beta2))

    # Define 1 discriminator and optimizer for each plane in each dimension
    netDs = []
    optDs = []
    for i in range(3):
        netD = Disc()
        netD = (nn.DataParallel(netD, list(range(ngpu)))).to(device)
        netDs.append(netD)
        optDs.append(optim.Adam(netDs[i].parameters(), lr=lr, betas=(beta1, beta2)))

    disc_real_log = []
    disc_fake_log = []
    gp_log = []
    Wass_log = []

    print("Starting Training Loop...")
    # For each epoch
    start = time.time()
    for epoch in range(num_epochs):
        # For each batch in the dataloader
        for i, ((datax,lblx), (datay,lbly), (dataz,lblz)) in enumerate(zip(dataloaderx, dataloadery, dataloaderz), 1):
            dataset = [datax, datay, dataz]
            lblset = [lblx,lbly,lblz]
            ### Initialise
            ### Discriminator
            ## Generate fake image batch with G
            noise = torch.randn(D_batch_size, nz, 4, 4, 4, device=device)
            fake_labels = torch.randint(1,3,(D_batch_size,nlabels,1,1,1), device = device)
            fake_labels = fake_labels.repeat(1, 1, 4, 4, 4).type_as(noise)
            fake_data = netG(noise, fake_labels).detach()

            # For each dimension
            start_disc = time.time()
            for dim, (netD, optimizer, data, lbl, d1, d2, d3) in enumerate(
                    zip(netDs, optDs, dataset, lblset, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                if isotropic:
                    netD = netDs[0]
                    optimizer = optDs[0]
                for p in netD.parameters():  # reset requires_grad
                    p.requires_grad_(True)
                ##train on real images
                real_data = data.to(device)
                netD.zero_grad()
                # return [real_data,lbl]
                # Forward pass real batch through D
                out_real = netD(real_data, lbl.repeat(1,1,64,64)).view(-1).mean()
                # train on fake images
                gradient_penalty = 0
                fake_data_perm = fake_data.permute(0, d1, 1, d2, d3).reshape(l * D_batch_size, nc, l, l)
                out_fake = netD(fake_data_perm, fake_labels.permute(0,2,1,3,4).repeat(1,16,1,16,16).reshape(-1,nlabels,64,64)).mean()
                gradient_penalty += Train_tools.cond_calc_gradient_penalty(netD, real_data, fake_data_perm[:batch_size],
                                                                      batch_size, l,
                                                                      device, Lambda, nc,
                                                                      fake_labels[:,:,:,:,0].repeat(l,1,16,16)[:batch_size].to(device),
                                                                      lbl.repeat(1,1,64,64).to(device),nlabels)

                disc_cost = out_fake - out_real + gradient_penalty
                disc_cost.backward()
                optimizer.step()

            disc_real_log.append(out_real.item())
            disc_fake_log.append(out_fake.item())
            Wass_log.append(out_real.item() - out_fake.item())
            gp_log.append(gradient_penalty.item())
            ### Generator Training
            start_gen = time.time()
            if i % int(critic_iters) == 0:
                netG.zero_grad()
                errG = 0
                noise = torch.randn(batch_size, nz, 4, 4, 4, device=device)
                fake_labels = torch.randint(1, 3, (batch_size, nlabels, 1, 1, 1), device=device)
                fake_labels = fake_labels.repeat(1, 1, 4, 4, 4).type_as(noise)
                fake = netG(noise, fake_labels)
                for dim, (netD, d1, d2, d3) in enumerate(
                        zip(netDs, [2, 3, 4], [3, 2, 2], [4, 4, 3])):
                    if isotropic:
                        netD = netDs[0]
                    for p in netD.parameters():
                        p.requires_grad_(False)
                    # For each plane
                    fake_data_perm = fake.permute(0, d1, 1, d2, d3).reshape(l * batch_size, nc, l, l)
                    output = netD(fake_data_perm, fake_labels.permute(0,2,1,3,4).repeat(1,16,1,16,16).reshape(-1,nlabels,64,64))
                    errG -= output.mean()
                    # Calculate gradients for G
                Gback_start = time.time()
                errG.backward()
                # print('Gback: ', time.time() - Gback_start)
                optG.step()
                # Output training stats & show imgs
                fin_gen = time.time() - start_gen
                # print('gen 1 run: ', fin_gen)
            if i % 25 == 0:
                start_save = time.time()
                torch.save(netG.state_dict(), pth + '_Gen.pt')
                torch.save(netD.state_dict(), pth + '_Disc.pt')
                noise = torch.randn(1, nz, 4, 4, 4, device=device)
                for tst_lbls in labels:
                    lbl = torch.zeros(1, nlabels, 4, 4, 4)
                    lbl_str = ''
                    for lb in range(nlabels):
                        lbl[:, lb] = tst_lbls[lb]+1
                        lbl_str += str(tst_lbls[lb])
                    img = netG(noise, lbl.type(torch.FloatTensor).cuda())

                    Train_tools.TestPlotter(img, 3, imtype, pth+lbl_str)

                ###Print progress
                ## calc ETA
                steps = len(dataloaderx)
                Train_tools.calc_ETA(steps, time.time(), start, i, epoch, num_epochs)
                ###save example slices
                # plotting graphs
                Train_tools.GraphPlot([disc_real_log, disc_fake_log], ['real', 'perp'], pth, 'LossGraph')
                Train_tools.GraphPlot([Wass_log], ['Wass Distance'], pth, 'WassGraph')
                Train_tools.GraphPlot([gp_log], ['Gradient Penalty'], pth, 'GpGraph')
                fin_save = time.time() - start_save
                # print('save: ', fin_save)
            if i % 300 == 0.5:
                noise = torch.randn(1, nz, 8, 8, 8, device=device)
                raw = netG(noise)
                gb = Train_tools.PostProc(raw, imtype)
                tif = np.int_(gb)
                tifffile.imwrite(pth + '_epoch_' + str(epoch) + '_iter_' + str(i) + '.tif', tif)