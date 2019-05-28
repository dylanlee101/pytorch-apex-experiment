import torch, time, argparse, pickle
import numpy as np
from torchvision.datasets import cifar
from torchvision.transforms import transforms
from VGG16 import VGG16
from utils import get_gpu_memory_map
from torch.utils.data import DataLoader
from apex.fp16_utils import *

parser = argparse.ArgumentParser()
parser.add_argument('--GPU', type=str, default='gpu_name')
parser.add_argument('--mode', type=str, default='FP32', choices=['FP32', 'ampO2', 'ampO3'])
parser.add_argument('--batch_size', type=int, default=1024)
parser.add_argument('--iteration', type=int, default=10)
args = parser.parse_args()

print('------------ Options -------------')
for k, v in sorted(vars(args).items()):
    print('%s: %s' % (str(k), str(v)))
print('-------------- End ---------------')

init_mem = get_gpu_memory_map()
seed = 7
np.random.seed(seed)
torch.manual_seed(seed)
torch.cuda.manual_seed_all(seed)
torch.backends.cudnn.benchmark = True
if args.mode in ['ampO2', 'ampO3']:
    from apex import amp
    amp_handle = amp.init()

transform = transforms.Compose([
    transforms.ToTensor(),
    transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
])
train_loader = DataLoader(cifar.CIFAR10(root='cifar', train=True, transform=transform, download=True), batch_size=args.batch_size, shuffle=False, pin_memory=True, drop_last=True)
test_loader = DataLoader(cifar.CIFAR10(root='cifar', train=False, transform=transform, download=True), batch_size=args.batch_size, shuffle=False, pin_memory=True, drop_last=True)
loss_fn = torch.nn.CrossEntropyLoss().cuda()
result = {}
result['train_time'] = []
result['train_mem'] = []
result['train_loss'] = []
result['test_time'] = []
result['test_loss'] = []
result['test_acc'] = []

model = VGG16(num_classes=10).cuda()

optimizer = torch.optim.SGD(model.parameters(), lr=0.01, momentum=0.9)
    
if args.mode == 'ampO2':
    model, optimizer = amp.initialize(model, optimizer,
                                              opt_level='O2',
#                                               keep_batchnorm_fp32=None ,
#                                               loss_scale="dynamic"
                                              )
elif args.mode == 'ampO3':
    model, optimizer = amp.initialize(model, optimizer,
                                              opt_level='O3',
    #                                           keep_batchnorm_fp32=None ,
    #                                           loss_scale="dynamic"
                                              )
    
for i in range(20):
    model.train()
    ll = []
    iteration = 0
    start_time = time.time()
    while not iteration == args.iteration:
        for x, y in train_loader:
            x, y = x.cuda(), y.cuda()

            optimizer.zero_grad()
            y_pred = model(x)
            loss = loss_fn(y_pred.float(), y)

            if args.mode == 'FP32':
                loss.backward()
            else:
                with amp.scale_loss(loss, optimizer) as scaled_loss:
                    scaled_loss.backward()
            optimizer.step()
            ll.append(loss.item())
            _, pred = torch.max(y_pred.data, 1)

            iteration += 1

            if iteration == args.iteration:
                break

    end_time = time.time() - start_time
    used_mem = get_gpu_memory_map() - init_mem
    print(args.mode)
    print('[%d-th]' % ((i+1)))
    print('Train time = %.2f' % (end_time))
    print('Train loss = %.4f' % (np.mean(ll)))
    print('GPU memory usage = %.2f' % (used_mem))
    result['train_time'].append(end_time)
    result['train_mem'].append(used_mem)
    result['train_loss'].append(np.mean(ll))

    start_time = time.time()
    with torch.no_grad():
        model.eval()
        loss = 0
        cor = 0
        total = 0
        for x, y in test_loader:
            x, y = x.cuda(), y.cuda()

            y_pred = model(x)
            loss += loss_fn(y_pred.float(), y).item()
            _, pred = torch.max(y_pred, 1)
            total += y.size(0)
            cor += (pred == y).sum().item()

    end_time = time.time() - start_time
    result['test_time'].append(end_time)
    result['test_loss'].append(loss / test_loader.__len__())
    result['test_acc'].append(cor / total * 100)

    print('Test time - %.2f' % (end_time))
    print('Test loss - %.4f' % (loss / test_loader.__len__()))
    print('Test acc - %.2f' % (cor / total * 100))
    print()

print('==================== Final results ====================')
print('Train time - mean: %.2f, std %.2f' % (np.mean(result['train_time']), np.std(result['train_time'])))
print('Train loss - mean: %.4f, std %.4f' % (np.mean(result['train_loss']), np.std(result['train_loss'])))
print('Used memory - mean: %.2f, std %.2f' % (np.mean(result['train_mem']), np.std(result['train_mem'])))
print('Test time - mean: %.2f, std %.2f' % (np.mean(result['test_time']), np.std(result['test_time'])))
print('Test loss - mean: %.4f' % result['test_loss'][-1], )
print('Test acc - mean: %.2f' % result['test_acc'][-1])
print('=======================================================')

# with open('result/' + args.GPU + '/CIFAR_' + args.mode + '_' + str(args.batch_size) + '_' + str(args.iteration) + '_result.pkl', 'wb') as f:
#     pickle.dump(result, f)
