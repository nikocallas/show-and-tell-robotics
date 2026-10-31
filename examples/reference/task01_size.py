def run(scene):
    for obj in scene.objects(size="large", fragile=False):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(size="small", fragile=False):
        pick(obj)
        place("right_bin")
