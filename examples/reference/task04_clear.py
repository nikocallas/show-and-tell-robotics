def run(scene):
    glass = scene.objects(shape="glass")
    avoid(glass)
    for obj in scene.objects(fragile=False):
        pick(obj)
        place("middle_bin")
