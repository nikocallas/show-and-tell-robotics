def run(scene):
    glass = scene.objects(shape="glass")
    for obj in scene.objects(size="large", fragile=False):
        pick(obj, avoid=glass)
        place("left_bin", avoid=glass)
    for obj in scene.objects(size="small", fragile=False):
        pick(obj, avoid=glass)
        place("right_bin", avoid=glass)
