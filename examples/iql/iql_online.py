# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.
"""IQL Example.

This is a self-contained example of an online IQL training script.

It works across Gym and DM-control over a variety of tasks.

Both state and pixel-based environments are supported.

The helper functions are coded in the utils.py associated with this script.

"""

import hydra
import tqdm
from torchrl.trainers.helpers.envs import correct_for_frame_skip

from utils import (
    get_stats,
    make_collector,
    make_iql_model,
    make_iql_optimizer,
    make_logger,
    make_loss,
    make_recorder,
    make_replay_buffer,
)


@hydra.main(config_path=".", config_name="online_config")
def main(cfg: "DictConfig"):  # noqa: F821

    cfg = correct_for_frame_skip(cfg)
    model_device = cfg.optim.device

    state_dict = get_stats(cfg.env)
    logger = make_logger(cfg.logger)
    replay_buffer = make_replay_buffer(cfg.replay_buffer)

    actor_network, qvalue_network, value_network = make_iql_model(cfg)
    policy = actor_network.to(model_device)
    qvalue_network = qvalue_network.to(model_device)
    value_network = value_network.to(model_device)

    collector = make_collector(cfg, state_dict=state_dict, policy=policy)
    loss, target_net_updater = make_loss(
        cfg.loss, policy, qvalue_network, value_network
    )
    optim = make_iql_optimizer(cfg.optim, policy, qvalue_network, value_network)
    recorder = make_recorder(cfg, logger, policy)

    optim_steps_per_batch = cfg.optim.optim_steps_per_batch
    batch_size = cfg.optim.batch_size
    init_random_frames = cfg.collector.init_random_frames
    record_interval = cfg.recorder.interval

    pbar = tqdm.tqdm(total=cfg.collector.total_frames)
    collected_frames = 0

    r0 = None
    l0 = None
    for data in collector:
        frames_in_batch = data.numel()
        collected_frames += frames_in_batch
        pbar.update(data.numel())
        # extend replay buffer
        replay_buffer.extend(data.view(-1))
        if collected_frames >= init_random_frames:
            for _ in range(optim_steps_per_batch):
                # sample
                sample = replay_buffer.sample(batch_size)
                # loss
                loss_vals = loss(sample)
                # backprop
                actor_loss = loss_vals["loss_actor"]
                q_loss = loss_vals["loss_qvalue"]
                value_loss = loss_vals["loss_value"]
                loss_val = actor_loss + q_loss + value_loss

                optim.zero_grad()
                loss_val.backward()
                optim.step()
                target_net_updater.step()

            if r0 is None:
                r0 = data["reward"].mean().item()
            if l0 is None:
                l0 = loss_val.item()

            for key, value in loss_vals.items():
                logger.log_scalar(key, value.item(), collected_frames)
            logger.log_scalar(
                "reward_training", data["reward"].mean().item(), collected_frames
            )

            pbar.set_description(
                f"loss: {loss_val.item(): 4.4f} (init: {l0: 4.4f}), reward: {data['reward'].mean(): 4.4f} (init={r0: 4.4f})"
            )
            collector.update_policy_weights_()
        if (
            collected_frames - frames_in_batch
        ) // record_interval < collected_frames // record_interval:
            recorder(batch=collected_frames)


if __name__ == "__main__":
    main()
