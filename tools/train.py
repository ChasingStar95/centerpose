from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import _init_paths

import os

import torch
import torch.utils.data
from models.model import create_model, load_model, save_model
from models.data_parallel import DataParallel
from logger import Logger
from datasets.dataset_factory import get_dataset
from trains.train_factory import train_factory

from config import cfg
from config import update_config

def main(cfg):
  torch.manual_seed(cfg.SEED)
  torch.backends.cudnn.benchmark = cfg.CUDNN.BENCHMARK
  Dataset = get_dataset(cfg.SAMPLE_METHOD, cfg.TASK)

  device = torch.device('cuda')
  logger = Logger(cfg)
  
  HEADS = dict(zip(cfg.MODEL.HEADS_NAME, cfg.MODEL.HEADS_NUM))
  print('Creating model...')
  model = create_model(cfg.MODEL.NAME, HEADS, cfg.MODEL.HEAD_CONV)
  optimizer = torch.optim.Adam(model.parameters(), cfg.TRAIN.LR)
  start_epoch = 0
  if cfg.MODEL.LOAD_MODEL != '':
    model, optimizer, start_epoch = load_model(
      model, cfg.MODEL.LOAD_MODEL, optimizer, cfg.TRAIN.RESUME, cfg.TRAIN.LR, cfg.TRAIN.LR_STEP)

  Trainer = train_factory[cfg.TASK]
  trainer = Trainer(cfg, model, optimizer)
  
  cfg.TRAIN.MASTER_BATCH_SIZE
  
  if cfg.TRAIN.MASTER_BATCH_SIZE == -1:
    master_batch_size = cfg.TRAIN.BATCH_SIZE // len(cfg.GPUS)
  else:
    master_batch_size = cfg.TRAIN.MASTER_BATCH_SIZE
  rest_batch_size = (cfg.TRAIN.BATCH_SIZE - master_batch_size)
  chunk_sizes = [cfg.TRAIN.MASTER_BATCH_SIZE]
  for i in range(len(cfg.GPUS) - 1):
    slave_chunk_size = rest_batch_size // (len(cfg.GPUS) - 1)
    if i < rest_batch_size % (len(cfg.GPUS) - 1):
      slave_chunk_size += 1
    chunk_sizes.append(slave_chunk_size)
  print(chunk_sizes)
  trainer.set_device(cfg.GPUS, chunk_sizes, device)

  print('Setting up data...')
  val_loader = torch.utils.data.DataLoader(
      Dataset(cfg, 'val'), 
      batch_size=1, 
      shuffle=False,
      num_workers=1,
      pin_memory=True
  )

  train_loader = torch.utils.data.DataLoader(
      Dataset(cfg, 'train'), 
      batch_size=cfg.TRAIN.BATCH_SIZE, 
      shuffle=True,
      num_workers=cfg.WORKERS,
      pin_memory=True,
      drop_last=True
  )

  print('Starting training...')
  best = 1e10
  for epoch in range(start_epoch + 1, cfg.TRAIN.EPOCHS + 1):
    mark = epoch if cfg.TRAIN.SAVE_ALL_MODEL else 'last'
    log_dict_train, _ = trainer.train(epoch, train_loader)
    logger.write('epoch: {} |'.format(epoch))
    for k, v in log_dict_train.items():
      logger.scalar_summary('train_{}'.format(k), v, epoch)
      logger.write('{} {:8f} | '.format(k, v))
    if cfg.TRAIN.VAL_INTERVALS > 0 and epoch % cfg.TRAIN.VAL_INTERVALS == 0:
      save_model(os.path.join(cfg.OUTPUT_DIR, 'model_{}.pth'.format(mark)), 
                 epoch, model, optimizer)
      with torch.no_grad():
        log_dict_val, preds = trainer.val(epoch, val_loader)
      for k, v in log_dict_val.items():
        logger.scalar_summary('val_{}'.format(k), v, epoch)
        logger.write('{} {:8f} | '.format(k, v))
      if log_dict_val[cfg.LOSS.METRIC] < best:
        best = log_dict_val[cfg.LOSS.METRIC]
        save_model(os.path.join(cfg.OUTPUT_DIR, 'model_best.pth'), 
                   epoch, model)
    else:
      save_model(os.path.join(cfg.OUTPUT_DIR, 'model_last.pth'), 
                 epoch, model, optimizer)
    logger.write('\n')
    if epoch in cfg.TRAIN.LR_STEP:
      save_model(os.path.join(cfg.OUTPUT_DIR, 'model_{}.pth'.format(epoch)), 
                 epoch, model, optimizer)
      lr = cfg.TRAIN.LR * (0.1 ** (cfg.TRAIN.LR_STEP.index(epoch) + 1))
      print('Drop LR to', lr)
      for param_group in optimizer.param_groups:
          param_group['lr'] = lr
  logger.close()

if __name__ == '__main__':
  config_name = '../experiments/dla_34_512x512_adam.yaml'
  update_config(cfg, config_name)
  main(cfg)
