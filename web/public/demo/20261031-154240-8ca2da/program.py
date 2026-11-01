def run(scene):
    glasses = scene.objects(shape="glass")
    avoid(glasses)
    for obj in scene.objects(size="large", fragile=False):
        pick(obj, avoid=glasses)
        place("left_bin", avoid=glasses)
    for obj in scene.objects(size="small", fragile=False):
        pick(obj, avoid=glasses)
        place("right_bin", avoid=glasses)
