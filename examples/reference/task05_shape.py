def run(scene):
    for obj in scene.objects(shape="cylinder"):
        pick(obj)
        place("left_bin")
    for obj in scene.objects(shape="cube"):
        pick(obj)
        place("right_bin")
